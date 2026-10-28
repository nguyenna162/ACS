"""Worker đồng bộ Aligo MES (tùy chọn, mặc định tắt khi offline).

Khi ``ALIGO_ENABLED=true``: lấy lệnh cấp vật tư từ Aligo, tạo SupplyRequest nội
bộ (không ghép cặp tức thời để worker pairing xử lý theo lô) và đẩy trạng thái
robot lên MES. Mọi lỗi mạng chỉ ghi log, không ảnh hưởng điều phối nội bộ.
"""

import logging

from django.conf import settings
from django.utils import timezone

from acs.services import SupplyService
from acs.models import SupplyRequest
from workers.schema_class import WorkerThread
from integrations.aligo import AligoClient, AligoError
from amr.models import RobotProfile

logger = logging.getLogger("workers.aligo_sync")


class AligoSyncWorker(WorkerThread):
    def __init__(self, thread_name="acs_aligo_sync", loop_sleep_s=None):
        super().__init__(
            thread_name=thread_name,
            worker_name=thread_name,
            loop_sleep_s=loop_sleep_s or getattr(settings, "ALIGO_POLL_INTERVAL_SECONDS", 3.0),
        )
        self.client = None
        self.last_sync = None

    def on_start(self) -> None:
        if not getattr(settings, "ALIGO_ENABLED", False):
            self.logger.info("Aligo đang tắt (ALIGO_ENABLED=false) - worker không chạy")
            return
        self.client = AligoClient()
        self.logger.info("Aligo sync bật: %s", self.client.base_url)

    def task(self) -> None:
        if self.client is None:
            return

        since = self.last_sync.isoformat() if self.last_sync else None
        try:
            commands = self.client.get_supply_commands(since=since)
        except AligoError as exc:
            self.logger.warning("Không lấy được lệnh Aligo: %s", exc)
            commands = []

        created = 0
        for command in commands:
            command_id = str(command.get("id") or command.get("commandId") or "").strip()
            if not command_id:
                continue
            request_id = f"ALIGO-{command_id}"
            existed = SupplyRequest.objects.filter(request_id=request_id).exists()
            SupplyService.create_request(
                request_id=request_id,
                source="ALIGO",
                line_id=str(command.get("lineId", "")),
                process=str(command.get("process", "")),
                product_model=str(command.get("productModel", "")),
                quantity=int(command.get("quantity", 1) or 1),
                priority=int(command.get("priority", 50) or 50),
                payload=command,
                auto_pair=False,
            )
            if not existed:
                created += 1

        self.last_sync = timezone.now()
        self._push_robot_status()
        if created:
            self.logger.info("Đã nhận %s lệnh cấp vật tư từ Aligo", created)

    def _push_robot_status(self) -> None:
        robots = [
            {
                "serialNumber": robot.serial_number,
                "operationalState": robot.operational_state,
                "batteryCharge": robot.battery_level,
                "position": {"x": robot.pos_x, "y": robot.pos_y, "theta": robot.theta},
                "orderId": robot.order_id,
            }
            for robot in RobotProfile.objects.all()[:200]
        ]
        try:
            self.client.push_robot_status({"robots": robots, "timestamp": timezone.now().isoformat()})
        except AligoError as exc:
            self.logger.warning("Không đẩy được trạng thái robot lên Aligo: %s", exc)
