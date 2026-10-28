"""Khung worker đa luồng theo tài liệu dự án (workers/schema_class.py).

Mỗi worker là một ``WorkerThread`` độc lập, có vòng lặp, nhịp nghỉ, log riêng và
tắt êm (graceful shutdown). Worker không tự mở kết nối MQTT: mọi bản tin đi qua
Transactional Outbox để bảo đảm không mất dữ liệu khi broker gián đoạn.
"""

import logging
import signal
import socket
import sys
import threading

from django.db import close_old_connections

logger = logging.getLogger("workers")


class WorkerThread(threading.Thread):
    def __init__(self, thread_name: str | None = None, worker_name: str | None = None,
                 loop_sleep_s: float = 0.5, logger_: logging.Logger | None = None):
        self.thread_name = thread_name or worker_name or self.__class__.__name__
        super().__init__(name=self.thread_name, daemon=True)
        self.worker_name = worker_name or self.thread_name
        self.loop_sleep_s = max(0.05, float(loop_sleep_s))
        self.logger = logger_ or logging.getLogger(f"workers.{self.worker_name}")
        self._stop_event = threading.Event()
        self.cycles = 0
        self.errors = 0
        self.last_error = ""

    # -- lifecycle ---------------------------------------------------------
    def stop(self) -> None:
        self._stop_event.set()

    def should_stop(self) -> bool:
        return self._stop_event.is_set()

    def run(self) -> None:
        self.logger.info("Worker %s bắt đầu", self.worker_name)
        try:
            self.on_start()
            while not self.should_stop():
                close_old_connections()
                try:
                    self.task()
                    self.errors = 0
                except Exception as exc:  # worker không được chết vì 1 bản ghi lỗi
                    self.errors += 1
                    self.last_error = str(exc)[:1000]
                    self.logger.exception("Worker %s lỗi chu kỳ", self.worker_name)
                    delay = min(30.0, self.loop_sleep_s * (2 ** min(self.errors, 5)))
                    self._stop_event.wait(delay)
                    continue
                self.cycles += 1
                self._stop_event.wait(self.loop_sleep_s)
        finally:
            try:
                self.on_stop()
            except Exception:  # pragma: no cover
                self.logger.exception("Worker %s lỗi khi dừng", self.worker_name)
            close_old_connections()
            self.logger.info("Worker %s đã dừng (cycles=%s)", self.worker_name, self.cycles)

    def on_start(self) -> None:
        """Hook khởi tạo tài nguyên (không bắt buộc)."""

    def on_stop(self) -> None:
        """Hook dọn dẹp tài nguyên (không bắt buộc)."""

    def task(self) -> None:
        raise NotImplementedError


class MQTTInterfaceWorker(WorkerThread):
    """Worker có nhu cầu gửi MQTT - ghi vào outbox thay vì gọi mạng trực tiếp."""

    def publish(self, topic: str, payload: dict, qos: int | None = None, retain: bool = False):
        from django.conf import settings
        from workers.acs_system import MqttOutbox  # import lười tránh vòng import

        return MqttOutbox.enqueue(
            topic,
            payload,
            qos=settings.MQTT_QOS if qos is None else qos,
            retain=retain,
        )

    def publish_many(self, items: list[dict]) -> int:
        from workers.acs_system import MqttOutbox  # import lười tránh vòng import

        return MqttOutbox.enqueue_many(items)


class HeartbeatThread(WorkerThread):
    """Ghi nhịp sống tiến trình để /health/ready phát hiện worker treo."""

    def __init__(self, component: str, interval_s: float = 5.0, meta_provider=None):
        super().__init__(thread_name=f"heartbeat-{component}", worker_name=component,
                         loop_sleep_s=interval_s)
        self.component = component
        self.meta_provider = meta_provider
        self.instance_id = f"{socket.gethostname()}:{component}"

    def on_start(self) -> None:
        from acs.models import SystemHeartbeat  # import lười tránh vòng import

        SystemHeartbeat.record(self.component, self.instance_id, {"host": socket.gethostname()})

    def task(self) -> None:
        from acs.models import SystemHeartbeat  # import lười tránh vòng import

        meta = {"host": socket.gethostname(), "pid": __import__("os").getpid()}
        if self.meta_provider:
            try:
                meta.update(self.meta_provider() or {})
            except Exception:
                self.logger.debug("Không lấy được meta heartbeat", exc_info=True)
        SystemHeartbeat.record(self.component, self.instance_id, meta)


def run_workers(workers: list[WorkerThread], component: str = "order_pipeline",
                meta_provider=None, heartbeat_interval_s: float = 5.0) -> int:
    """Chạy danh sách worker + heartbeat cho tới khi nhận SIGINT/SIGTERM."""
    heartbeat = HeartbeatThread(component, heartbeat_interval_s, meta_provider)
    all_workers = list(workers) + [heartbeat]
    stop_event = threading.Event()

    def _handle_signal(signum, frame):  # noqa: ARG001
        logger.info("Nhận tín hiệu %s - đang dừng worker...", signum)
        stop_event.set()

    previous_handlers = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[sig] = signal.signal(sig, _handle_signal)
        except (ValueError, AttributeError):  # pragma: no cover - môi trường đặc biệt
            pass
    if sys.platform == "win32":
        try:
            previous_handlers[signal.SIGBREAK] = signal.signal(signal.SIGBREAK, _handle_signal)
        except (ValueError, AttributeError):  # pragma: no cover
            pass

    try:
        for worker in all_workers:
            worker.start()
        while not stop_event.wait(1.0):
            if not any(worker.is_alive() for worker in workers):
                logger.error("Tất cả worker đã dừng ngoài ý muốn")
                break
    finally:
        for worker in all_workers:
            worker.stop()
        for worker in all_workers:
            worker.join(timeout=10)
        for sig, handler in previous_handlers.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, AttributeError):  # pragma: no cover
                pass
    logger.info("Đã dừng toàn bộ worker (%s)", component)
    return 0

