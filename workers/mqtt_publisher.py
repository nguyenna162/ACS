"""Worker gửi MQTT từ Transactional Outbox (at-least-once, có backoff)."""

import json
import logging

from django.conf import settings

from workers.acs_system import create_client
from workers.acs_system import MqttOutbox
from workers.schema_class import WorkerThread

logger = logging.getLogger("workers.outbox_publisher")


class OutboxPublisherWorker(WorkerThread):
    def __init__(self, thread_name="mqtt_outbox_publisher", loop_sleep_s=0.2):
        super().__init__(thread_name=thread_name, worker_name=thread_name,
                         loop_sleep_s=loop_sleep_s)
        self.client = None

    def on_start(self) -> None:
        self.client = create_client("outbox", clean_session=True, with_last_will=False)
        try:
            self.client.connect_async(settings.MQTT_BROKER, int(settings.MQTT_PORT),
                                      int(settings.MQTT_KEEPALIVE))
            self.client.loop_start()
        except Exception:
            self.logger.exception("Không khởi tạo được MQTT publisher")

    def task(self) -> None:
        if self.client is None:
            return
        batch = MqttOutbox.claim(int(getattr(settings, "ACS_OUTBOX_BATCH_SIZE", 50)))
        if not batch:
            return
        sent_ids: list[int] = []
        failed: dict[int, str] = {}
        for message in batch:
            try:
                info = self.client.publish(
                    message.topic,
                    json.dumps(message.payload, ensure_ascii=False),
                    qos=int(message.qos),
                    retain=bool(message.retain),
                )
                info.wait_for_publish(timeout=2.0)
                if info.is_published():
                    sent_ids.append(message.id)
                else:
                    failed[message.id] = "Timeout chờ broker xác nhận publish"
            except Exception as exc:
                failed[message.id] = str(exc)[:500]

        if sent_ids:
            MqttOutbox.mark_sent(sent_ids)
        for message_id, error in failed.items():
            MqttOutbox.mark_failure(
                [message_id], error,
                max_attempts=int(getattr(settings, "ACS_OUTBOX_MAX_ATTEMPTS", 20)),
            )
        if failed:
            self.logger.warning("%s/%s bản tin gửi lỗi", len(failed), len(batch))

    def on_stop(self) -> None:
        if self.client is None:
            return
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:  # pragma: no cover
            pass
