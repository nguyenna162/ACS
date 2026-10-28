"""Xử lý trạng thái AMR (VDA 5050) và đồng bộ vòng đời đơn hàng.

Thiết kế cho 50 AMR @1Hz:
* Không ghi log CSDL cho mọi bản tin: telemetry được lấy mẫu theo
  ``ACS_TELEMETRY_SAMPLE_SECONDS``.
* WebSocket nhận snapshot 1s/lần từ consumer; broadcast trực tiếp được giới hạn
  theo ``ACS_WS_SNAPSHOT_INTERVAL_SECONDS``.
* Mọi cập nhật trạng thái robot là một UPDATE gọn (không kéo theo save toàn bộ).
"""

import logging
import time

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.utils import timezone

from workers.acs_system import TOPIC_ALERT, TOPIC_ORDER, EventBus
from map.models import Node
from amr.models import RobotProfile, RobotTelemetry

logger = logging.getLogger("amr.services")

_last_broadcast: dict[str, float] = {}
_last_telemetry: dict[str, float] = {}
_unknown_logged: dict[str, float] = {}
_CACHE_LIMIT = 5000


def _warn_unknown_robot(serial_number: str, message: str) -> None:
    """Giới hạn log AMR lạ (1 lần/phút/robot) tránh ngập log khi cấu hình sai."""
    now = time.monotonic()
    last = _unknown_logged.get(serial_number, 0.0)
    if now - last < 60.0:
        return
    if len(_unknown_logged) > _CACHE_LIMIT:
        _unknown_logged.clear()
    _unknown_logged[serial_number] = now
    logger.warning(message, serial_number)


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _derive_state(payload: dict, errors: list) -> str:
    safety = _as_dict(payload.get("safetyState"))
    if safety.get("eStop") in ("AUTOMATIC", "MANUAL"):
        return "ERROR"
    for error in errors:
        if isinstance(error, dict) and str(error.get("errorLevel", "")).upper() == "FATAL":
            return "ERROR"
    if errors:
        return "WARNING"
    if payload.get("driving"):
        return "RUNNING"
    return "IDLE"


class RobotService:
    # -- broadcast --------------------------------------------------------
    @staticmethod
    def _should_broadcast(serial_number: str) -> bool:
        interval = float(getattr(settings, "ACS_WS_SNAPSHOT_INTERVAL_SECONDS", 1.0))
        now = time.monotonic()
        last = _last_broadcast.get(serial_number, 0.0)
        if now - last < interval:
            return False
        if len(_last_broadcast) > _CACHE_LIMIT:
            _last_broadcast.clear()
        _last_broadcast[serial_number] = now
        return True

    @staticmethod
    def broadcast_telemetry(robot: RobotProfile, telemetry_data: dict | None = None):
        if not RobotService._should_broadcast(robot.serial_number):
            return
        channel_layer = get_channel_layer()
        if not channel_layer:
            return
        payload = {
            "type": "robot_update",
            "data": telemetry_snapshot(robot, telemetry_data),
        }
        try:
            async_to_sync(channel_layer.group_send)("robot_telemetry", payload)
        except Exception:  # pragma: no cover - layer không sẵn sàng
            logger.debug("Không broadcast được telemetry", exc_info=True)

    # -- connection -------------------------------------------------------
    @staticmethod
    def handle_connection(manufacturer: str, serial_number: str, payload: dict):
        state = str(payload.get("connectionState", "OFFLINE")).upper()
        robot = RobotProfile.objects.filter(pk=serial_number).first()
        if robot is None:
            if not getattr(settings, "ACS_AUTO_REGISTER_ROBOTS", True) or state != "ONLINE":
                _warn_unknown_robot(serial_number, "Bỏ qua connection của AMR chưa khai báo: %s")
                return None
            robot = RobotProfile.objects.create(
                serial_number=serial_number,
                manufacturer=manufacturer or settings.ACS_DEFAULT_MANUFACTURER,
                operational_state="IDLE",
                is_online=True,
                last_heartbeat=timezone.now(),
            )
            logger.info("Tự đăng ký AMR %s từ bản tin connection", serial_number)

        now = timezone.now()
        if state in ("OFFLINE", "CONNECTIONBROKEN"):
            robot.mark_offline(now)
            EventBus.publish(
                TOPIC_ALERT,
                {"event": "robot_offline", "amr": serial_number, "state": state},
            )
        else:
            RobotProfile.objects.filter(pk=serial_number).update(
                is_online=True,
                last_heartbeat=now,
                operational_state=(
                    "IDLE" if robot.operational_state == "OFFLINE" else robot.operational_state
                ),
                is_idle=(robot.order_id == ""),
            )
            robot.refresh_from_db(
                fields=["is_online", "last_heartbeat", "operational_state", "is_idle"]
            )

        RobotService.broadcast_telemetry(robot, {"connectionState": state})
        return robot

    # -- state ------------------------------------------------------------
    @staticmethod
    def apply_state(manufacturer: str, serial_number: str, payload: dict,
                    record_telemetry: bool = True, allow_register: bool | None = None):
        """Áp dụng bản tin VDA 5050 ``state`` vào hồ sơ AMR.

        Trả về ``(robot, changed)``. Bản tin cũ (headerId nhỏ hơn bản đã xử lý)
        bị bỏ qua để không ghi đè vị trí/đơn hàng hiện tại.
        """
        allow_register = (
            settings.ACS_AUTO_REGISTER_ROBOTS if allow_register is None else allow_register
        )
        now = timezone.now()
        robot = RobotProfile.objects.filter(pk=serial_number).first()
        created = False
        if robot is None:
            if not allow_register:
                _warn_unknown_robot(serial_number, "AMR chưa khai báo, bỏ qua state: %s")
                return None, False
            robot = RobotProfile(
                serial_number=serial_number,
                manufacturer=manufacturer or settings.ACS_DEFAULT_MANUFACTURER,
                last_heartbeat=now,
            )
            created = True

        header_id = int(payload.get("headerId") or 0)
        if not created and header_id and robot.last_state_seq and header_id < robot.last_state_seq:
            logger.debug(
                "Bỏ qua state cũ của %s (headerId %s < %s)",
                serial_number, header_id, robot.last_state_seq,
            )
            return robot, False

        errors = _as_list(payload.get("errors"))
        position = _as_dict(payload.get("agvPosition"))
        battery = _as_dict(payload.get("batteryState"))
        safety = _as_dict(payload.get("safetyState"))
        velocity = _as_dict(payload.get("velocity"))
        last_node = str(payload.get("lastNodeId") or robot.last_node_id or "")
        node_exists = bool(last_node) and Node.objects.filter(pk=last_node).exists()

        derived = _derive_state(payload, errors)
        if robot.operational_state == "BLOCKED" and derived in ("RUNNING", "IDLE") and not errors:
            derived = "BLOCKED"

        battery_charge = battery.get("batteryCharge")
        try:
            battery_level = (
                float(battery_charge) if battery_charge is not None else robot.battery_level
            )
        except (TypeError, ValueError):
            battery_level = robot.battery_level

        robot.manufacturer = (
            payload.get("manufacturer")
            or robot.manufacturer
            or manufacturer
            or settings.ACS_DEFAULT_MANUFACTURER
        )
        robot.order_id = str(payload.get("orderId") or "")
        robot.order_update_id = int(payload.get("orderUpdateId") or 0)
        robot.last_state_seq = header_id or robot.last_state_seq
        robot.node_states = _as_list(payload.get("nodeStates"))
        robot.edge_states = _as_list(payload.get("edgeStates"))
        robot.action_state = _as_list(payload.get("actionStates"))
        robot.loads = _as_list(payload.get("loads"))
        robot.errors = errors
        robot.information = _as_list(payload.get("information"))
        robot.amr_position = position
        robot.velocity = velocity
        robot.battery_state = battery
        robot.safety_state = safety
        robot.driving = bool(payload.get("driving"))
        robot.paused = bool(payload.get("paused"))
        robot.new_base_request = bool(payload.get("newBaseRequest"))
        robot.operating_mode = (
            payload.get("operatingMode")
            if payload.get("operatingMode") in dict(RobotProfile.OPERATING_MODES)
            else robot.operating_mode
        )
        robot.zone_set_id = str(payload.get("zoneSetId") or "")
        robot.operational_state = derived
        robot.battery_level = battery_level
        robot.pos_x = float(position.get("x", robot.pos_x) or 0.0)
        robot.pos_y = float(position.get("y", robot.pos_y) or 0.0)
        robot.theta = float(position.get("theta", robot.theta) or 0.0)
        robot.last_node_id = last_node
        if node_exists:
            robot.current_node_id = last_node
        robot.is_online = True
        robot.is_idle = derived == "IDLE" and not robot.order_id
        robot.last_heartbeat = now
        robot.state_received_at = now
        robot.save()

        if record_telemetry:
            RobotService._record_telemetry(robot, payload, derived)

        if robot.order_id:
            RobotService.sync_active_order(robot, payload)
        if derived in ("ERROR", "WARNING"):
            EventBus.publish(
                TOPIC_ALERT,
                {
                    "event": "robot_state_alert",
                    "amr": robot.serial_number,
                    "state": derived,
                    "errors": errors[:5],
                },
            )
        RobotService.broadcast_telemetry(robot)
        return robot, True

    @staticmethod
    def _record_telemetry(robot: RobotProfile, payload: dict, derived: str) -> None:
        interval = float(getattr(settings, "ACS_TELEMETRY_SAMPLE_SECONDS", 5.0))
        now = time.monotonic()
        should_record = (now - _last_telemetry.get(robot.serial_number, 0.0)) >= interval
        if not should_record and derived not in ("ERROR", "WARNING"):
            return
        if len(_last_telemetry) > _CACHE_LIMIT:
            _last_telemetry.clear()
        _last_telemetry[robot.serial_number] = now
        RobotTelemetry.objects.create(
            robot=robot,
            order_id=robot.order_id,
            order_update_id=robot.order_update_id,
            operating_mode=robot.operating_mode,
            velocity_vx=float(_as_dict(payload.get("velocity")).get("vx", 0.0) or 0.0),
            node_states=robot.node_states,
            edge_states=robot.edge_states,
            action_state=robot.action_state,
            loads=robot.loads,
            errors=robot.errors,
            information=robot.information,
            amr_position=robot.amr_position,
            velocity=robot.velocity,
            battery_state=robot.battery_state,
            safety_state=robot.safety_state,
            driving=robot.driving,
            paused=robot.paused,
            new_base_request=robot.new_base_request,
            error_codes=robot.errors,
        )

    # -- vòng đời đơn hàng ------------------------------------------------
    @staticmethod
    def sync_active_order(robot: RobotProfile, payload: dict) -> None:
        from acs.models import Order
        from acs.services import OrderMasterService
        from buffer.models import BufferSlot
        from map.traffic_lock import TrafficController

        last_node = payload.get("lastNodeId")
        if not last_node:
            return
        order = Order.objects.filter(pk=robot.order_id, robot=robot).first()
        if order is None or order.is_terminal:
            return

        node_ids = list(
            order.order_nodes.order_by("sequence_id").values_list("node_id", flat=True)
        )
        if not node_ids:
            return

        now = timezone.now()
        if last_node == node_ids[-1]:
            Order.objects.filter(pk=order.pk).update(
                order_status="COMPLETED",
                completed_at=now,
                order_update_id=robot.order_update_id,
            )
            src_slot = order.src_slot
            dst_slot = order.dst_slot
            if src_slot.slot_status == "OCCUPIED":
                pallet = src_slot.pallet_code or "PALLET_DEFAULT"
                BufferSlot.objects.filter(pk=src_slot.pk).update(
                    slot_status="EMPTY",
                    pallet_code=None,
                    handle_order=None,
                    last_success_transfer=now,
                )
                BufferSlot.objects.filter(pk=dst_slot.pk).update(
                    slot_status="OCCUPIED",
                    pallet_code=pallet,
                    handle_order=None,
                    last_success_transfer=now,
                )
            TrafficController.release_order_locks(order)
            OrderMasterService.complete_from_order(order)
            logger.info("Đơn %s hoàn thành tại node %s", order.order_id, last_node)
            return

        changed = TrafficController.update_sliding_window(order, last_node)
        if changed:
            from acs.vda5050 import build_instant_action_payload, build_order_payload
            from workers.acs_system import next_header_id, order_topic
            from workers.acs_system import MqttOutbox

            topic = order_topic(robot.manufacturer, robot.serial_number)
            MqttOutbox.enqueue(
                topic,
                build_order_payload(order, header_id=next_header_id(topic)),
                qos=settings.MQTT_QOS,
            )
            logger.debug(
                "Phát cập nhật sliding window cho đơn %s tại %s", order.order_id, last_node
            )

    # -- heartbeat --------------------------------------------------------
    @staticmethod
    def monitor_heartbeats() -> int:
        now = timezone.now()
        warning_after = float(getattr(settings, "ACS_HEARTBEAT_WARNING_SECONDS", 5.0))
        offline_after = float(getattr(settings, "ACS_HEARTBEAT_OFFLINE_SECONDS", 10.0))
        changed = 0
        for robot in RobotProfile.objects.exclude(operational_state="OFFLINE"):
            if not robot.last_heartbeat:
                delta = offline_after + 1
            else:
                delta = (now - robot.last_heartbeat).total_seconds()
            if delta > offline_after:
                robot.mark_offline(now)
                EventBus.publish(
                    TOPIC_ALERT,
                    {"event": "robot_offline", "amr": robot.serial_number,
                     "reason": "heartbeat_timeout"},
                )
                logger.warning("AMR %s mất heartbeat %.1fs -> OFFLINE", robot.serial_number, delta)
                changed += 1
            elif delta > warning_after and robot.operational_state not in (
                "WARNING", "ERROR", "BLOCKED",
            ):
                RobotProfile.objects.filter(pk=robot.serial_number).update(
                    operational_state="WARNING"
                )
                changed += 1
        return changed


def telemetry_snapshot(robot: RobotProfile, extra: dict | None = None) -> dict:
    """Ảnh chụp trạng thái AMR cho WebSocket/HMI (1s/lần)."""
    data = {
        "serial_number": robot.serial_number,
        "manufacturer": robot.manufacturer,
        "operational_state": robot.operational_state,
        "battery_level": robot.battery_level,
        "pos_x": robot.pos_x,
        "pos_y": robot.pos_y,
        "theta": robot.theta,
        "current_node_id": robot.current_node_id,
        "last_node_id": robot.last_node_id,
        "last_heartbeat": robot.last_heartbeat.isoformat() if robot.last_heartbeat else None,
        "is_online": robot.is_online,
        "is_idle": robot.is_idle,
        "order_id": robot.order_id,
        "order_update_id": robot.order_update_id,
        "operating_mode": robot.operating_mode,
        "driving": robot.driving,
        "paused": robot.paused,
        "new_base_request": robot.new_base_request,
        "zone_set_id": robot.zone_set_id,
        "errors": robot.errors,
        "safety_state": robot.safety_state,
        "battery_state": robot.battery_state,
        "velocity": robot.velocity,
        "amr_position": robot.amr_position,
        "node_states": robot.node_states,
        "edge_states": robot.edge_states,
        "action_state": robot.action_state,
        "loads": robot.loads,
        "information": robot.information,
        "is_used_by_master": robot.is_used_by_master_id,
    }
    if extra:
        data.update(extra)
    return data



