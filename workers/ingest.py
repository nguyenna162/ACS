"""Worker nhận trạng thái AMR qua MQTT (VDA 5050 state/connection)."""

import json
import logging
import time

from django.conf import settings

from workers.acs_system import (
    connection_wildcard_topic,
    controller_status_topic,
    create_client,
    parse_topic,
    state_wildcard_topic,
)
from workers.schema_class import WorkerThread
from amr.services import RobotService

logger = logging.getLogger("workers.ingest")


class MqttIngestWorker(WorkerThread):
    """Giữ kết nối MQTT, subscribe state/connection và áp dụng vào DB."""

    def __init__(self, thread_name="mqtt_ingest", loop_sleep_s=1.0):
        super().__init__(thread_name=thread_name, worker_name=thread_name,
                         loop_sleep_s=loop_sleep_s)
        self.client = None
        self.connected = False
        self.messages = 0
        self.parse_errors = 0
        self._last_reconnect_attempt = 0.0

    def on_start(self) -> None:
        self.client = create_client("ingest")
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        try:
            self.client.connect_async(settings.MQTT_BROKER, int(settings.MQTT_PORT),
                                      int(settings.MQTT_KEEPALIVE))
            self.client.loop_start()
        except Exception:
            self.logger.exception("Không khởi tạo được MQTT ingest")

    def task(self) -> None:
        if self.client is None or self.connected:
            return
        now = time.monotonic()
        if now - self._last_reconnect_attempt < 5.0:
            return
        self._last_reconnect_attempt = now
        try:
            self.client.reconnect()
        except Exception as exc:
            self.logger.debug("Chưa kết nối lại được MQTT: %s", exc)

    def on_stop(self) -> None:
        if self.client is None:
            return
        try:
            self.client.publish(
                controller_status_topic(),
                json.dumps({"state": "OFFLINE", "instance": settings.ACS_INSTANCE_NAME}),
                qos=1,
                retain=True,
            )
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:  # pragma: no cover
            self.logger.debug("Lỗi khi đóng MQTT ingest", exc_info=True)

    # -- callbacks --------------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = True
        self.logger.info("Đã kết nối MQTT broker %s:%s (%s)",
                         settings.MQTT_BROKER, settings.MQTT_PORT, reason_code)
        client.subscribe(state_wildcard_topic(), qos=settings.MQTT_QOS)
        client.subscribe(connection_wildcard_topic(), qos=settings.MQTT_QOS)
        client.publish(
            controller_status_topic(),
            json.dumps({"state": "ONLINE", "instance": settings.ACS_INSTANCE_NAME}),
            qos=1,
            retain=True,
        )

    def _on_disconnect(self, client, userdata, *args):
        self.connected = False
        self.logger.warning("Mất kết nối MQTT broker, sẽ tự kết nối lại")

    def _on_message(self, client, userdata, message):
        try:
            info = parse_topic(message.topic)
            if not info:
                return
            payload = json.loads(message.payload.decode("utf-8"))
            self.messages += 1

            if info["channel"] == "connection":
                RobotService.handle_connection(
                    info["manufacturer"], info["serial_number"], payload
                )
            elif info["channel"] == "state":
                robot, _changed = RobotService.apply_state(
                    info["manufacturer"], info["serial_number"], payload
                )
                if robot is None:
                    self.logger.debug(
                        "Bỏ qua state AMR chưa khai báo: %s", info["serial_number"]
                    )
        except json.JSONDecodeError:
            self.parse_errors += 1
            self.logger.warning("Bản tin MQTT không phải JSON: %s", message.topic)
        except Exception:
            self.parse_errors += 1
            self.logger.exception("Lỗi xử lý bản tin MQTT %s", message.topic)
