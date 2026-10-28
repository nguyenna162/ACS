"""Worker gửi đơn SubOrder qua MQTT + theo dõi ack/resend/timeout.

Mô phỏng thiết kế trong tài liệu (``workers/acs_order_send.py``):

* ``SubOrderDispatcher``: quét OrderMaster đang xử lý, dựng bản tin VDA 5050
  cho từng SubOrder PENDING, đăng ký ``ActionDispatch`` và đẩy vào MQTT Outbox.
* ``OrderSendMqtt``: xác nhận ACK từ trạng thái AMR (orderId/orderUpdateId/
  newBaseRequest), gửi lại mỗi ``ACS_RESEND_INTERVAL_SECONDS`` và đánh dấu
  thất bại sau ``ACS_SENT_TIMEOUT_SECONDS``.
* ``SubOrderSentTimeoutChecker``: quét SubOrder kẹt ở SENT quá hạn.
"""

import logging
import threading

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from acs.models import ActionDispatch, OrderMaster, SubOrder
from acs.services import OrderMasterService
from acs.vda5050 import build_instant_action_payload, build_order_payload
from workers.acs_system import order_topic
from workers.schema_class import MQTTInterfaceWorker, WorkerThread
from amr.models import RobotProfile, TopicControl

logger = logging.getLogger("workers.order_send")

SENT_TIMEOUT_SECONDS = float(getattr(settings, "ACS_SENT_TIMEOUT_SECONDS", 300.0))
RESEND_INTERVAL_SECONDS = float(getattr(settings, "ACS_RESEND_INTERVAL_SECONDS", 3.0))
MAX_RAM_CACHE_SIZE = int(getattr(settings, "ACS_MAX_CACHE_SIZE", 20000))

_anchor_lock = threading.Lock()
_sent_anchor_history: dict[str, dict[int, int]] = {}
_last_sent_anchor_seq: dict[str, int] = {}
_dispatched_lock = threading.Lock()
_dispatched_in_flight: set[str] = set()


def _remember_anchor(key: str, order_update_id: int, anchor_seq: int) -> None:
    with _anchor_lock:
        history = _sent_anchor_history.setdefault(key, {})
        history[int(order_update_id)] = int(anchor_seq)
        _last_sent_anchor_seq[key] = int(anchor_seq)
        if len(_last_sent_anchor_seq) > MAX_RAM_CACHE_SIZE:
            _last_sent_anchor_seq.pop(next(iter(_last_sent_anchor_seq)), None)
        if len(history) > 50:
            history.pop(next(iter(history)), None)


def _forget_anchor(key: str) -> None:
    with _anchor_lock:
        _sent_anchor_history.pop(key, None)
        _last_sent_anchor_seq.pop(key, None)


def resolve_anchor_seq(sub_order: SubOrder, robot: RobotProfile) -> int | None:
    """Xác định anchor (sequenceId đã được AMR xác nhận) giống tài liệu."""
    order = sub_order.order
    if order is None or robot.order_id != str(order.order_id):
        return None
    if not robot.new_base_request:
        return None

    nodes = (sub_order.path or {}).get("nodes") or []
    sequence_by_node: dict[str, int] = {}
    if isinstance(nodes, list):
        for index, node_id in enumerate(nodes):
            sequence_by_node[str(node_id)] = index * 2

    key = str(sub_order.sub_order_id)
    with _anchor_lock:
        history = dict(_sent_anchor_history.get(key, {}))
        last_anchor = _last_sent_anchor_seq.get(key)

    accepted_update = int(robot.order_update_id or 0)
    anchor = history.get(accepted_update, last_anchor)
    current_seq = sequence_by_node.get(robot.last_node_id or "") or sequence_by_node.get(
        robot.current_node_id or ""
    )
    if current_seq is not None:
        anchor = max(int(anchor or 0), int(current_seq))
    return int(anchor) if anchor is not None else None


def amr_ready_for_update(sub_order: SubOrder, robot: RobotProfile) -> bool:
    """AMR sẵn sàng nhận đơn mới/cập nhật của sub-order này hay không."""
    if robot.operational_state in ("OFFLINE", "ERROR", "INIT"):
        return False
    order = sub_order.order
    if order is None:
        return False
    if robot.order_id and robot.order_id != str(order.order_id):
        return False
    if robot.order_id == str(order.order_id):
        return bool(robot.new_base_request) or sub_order.sent_at is None
    return True


def register_dispatched_actions(sub_order: SubOrder, robot: RobotProfile,
                                order_payload: dict, log=logger) -> int:
    """Tạo ActionDispatch cho mọi action trong bản tin VDA 5050 (idempotent)."""
    created_count = 0
    for node in order_payload.get("nodes", []) or []:
        for action in node.get("actions", []) or []:
            action_id = action.get("actionId")
            if not action_id:
                continue
            _dispatch, created = ActionDispatch.objects.get_or_create(
                action_id=action_id,
                defaults={
                    "sub_order": sub_order,
                    "order": sub_order.order,
                    "amr": robot,
                    "action_type": action.get("actionType", ""),
                    "blocking_type": action.get("blockingType") or "NONE",
                    "parameters": action.get("actionParameters") or [],
                },
            )
            created_count += int(created)
    if created_count:
        log.info(
            "Đăng ký %s action dispatch cho %s", created_count, sub_order.sub_order_id
        )
    return created_count


def _header_id_for(robot: RobotProfile) -> int:
    topic = order_topic(robot.manufacturer, robot.serial_number)
    control = TopicControl.objects.filter(topic=topic).first()
    if control is None:
        control = TopicControl.objects.create(
            topic=topic,
            topic_type="ORDER",
            amr=robot,
            manufacturer=robot.manufacturer,
            version=robot.version,
        )
    header_id = control.next_header_id()
    TopicControl.objects.filter(pk=control.pk).update(last_call=timezone.now())
    return header_id


def build_sub_order_payload(sub_order: SubOrder, header_id: int | None = None) -> dict:
    script_order = sub_order.order
    robot = sub_order.amr
    payload = build_order_payload(
        script_order,
        header_id=header_id if header_id is not None else _header_id_for(robot),
    )
    payload["orderUpdateId"] = int(sub_order.order_update_id or 0)
    zone_set_id = (sub_order.path or {}).get("zoneSetId")
    if zone_set_id:
        payload["zoneSetId"] = zone_set_id
    return payload


class SubOrderDispatcher(MQTTInterfaceWorker):
    """Phát đơn VDA 5050 cho các SubOrder PENDING (mỗi AMR một chặng)."""

    def __init__(self, thread_name="suborder_dispatcher",
                 worker_name="mqtt_send_suborder", loop_sleep_s=None):
        super().__init__(
            thread_name=thread_name,
            worker_name=worker_name,
            loop_sleep_s=loop_sleep_s or getattr(settings, "ACS_WORKER_LOOP_SLEEP_SECONDS", 0.5),
        )

    def task(self) -> None:
        pool = int(getattr(settings, "ACS_ORDER_MASTER_POOL", 3))
        active_masters = OrderMaster.objects.filter(
            status__in=["PLANNING", "PROCESSING"]
        ).order_by("created_at", "id")[:pool]
        if not active_masters:
            return

        sub_orders = (
            SubOrder.objects.filter(
                order_master__in=active_masters,
                status="PENDING",
                order__isnull=False,
                amr__isnull=False,
            )
            .select_related("order", "amr", "order_master")
            .order_by("created_at", "id")[:20]
        )
        for sub_order in sub_orders:
            key = str(sub_order.sub_order_id)
            with _dispatched_lock:
                if key in _dispatched_in_flight:
                    continue
            robot = sub_order.amr
            if not amr_ready_for_update(sub_order, robot):
                continue
            try:
                payload = build_sub_order_payload(sub_order)
                register_dispatched_actions(sub_order, robot, payload, self.logger)
                topic = order_topic(robot.manufacturer, robot.serial_number)
                self.publish(topic, payload, qos=settings.MQTT_QOS)
                anchor = resolve_anchor_seq(sub_order, robot) or 0
                sub_order.mark_sent(anchor_seq=anchor)
                _remember_anchor(key, sub_order.order_update_id, anchor)
                with _dispatched_lock:
                    _dispatched_in_flight.add(key)
                self.logger.info(
                    "Đã phát order %s -> %s (anchor=%s)",
                    sub_order.order_id,
                    robot.serial_number,
                    anchor,
                )
            except Exception as exc:
                self.logger.warning(
                    "Không phát được sub-order %s: %s", sub_order.sub_order_id, exc
                )
                sub_order.outcome = str(exc)[:255]
                sub_order.save(update_fields=["outcome", "updated_at"])


class OrderSendMqtt(MQTTInterfaceWorker):
    """Theo dõi ACK/gửi lại/timeout cho các SubOrder đã phát."""

    def __init__(self, thread_name="mqtt_send_suborder_ack",
                 worker_name="mqtt_ack_suborder", loop_sleep_s=None):
        super().__init__(
            thread_name=thread_name,
            worker_name=worker_name,
            loop_sleep_s=loop_sleep_s or getattr(settings, "ACS_WORKER_LOOP_SLEEP_SECONDS", 0.5),
        )

    def task(self) -> None:
        sub_orders = (
            SubOrder.objects.filter(status__in=["SENT", "ACKED"])
            .select_related("order", "amr", "order_master")
            .order_by("last_sent_at", "id")[:50]
        )
        for sub_order in sub_orders:
            try:
                self._process(sub_order)
            except Exception:
                self.logger.exception(
                    "Lỗi xử lý ACK cho sub-order %s", sub_order.sub_order_id
                )

    @staticmethod
    def _check_acked(sub_order: SubOrder) -> str:
        robot = sub_order.amr
        order = sub_order.order
        if sub_order.is_terminal or order is None or robot is None:
            return "DROP"
        if order.order_status in ("FAILED", "CANCELLED"):
            return "DROP"
        if robot.order_id == str(order.order_id) and int(robot.order_update_id or 0) >= int(
            sub_order.order_update_id or 0
        ):
            return "ACKED"
        return "PENDING"

    def _process(self, sub_order: SubOrder) -> None:
        key = str(sub_order.sub_order_id)
        status = self._check_acked(sub_order)

        if status == "DROP":
            sub_order.mark_failed(sub_order.outcome or "Đơn đã bị hủy/không còn hiệu lực")
            _forget_anchor(key)
            with _dispatched_lock:
                _dispatched_in_flight.discard(key)
            return

        if status == "ACKED":
            if sub_order.status != "ACKED":
                sub_order.mark_acked()
            order = sub_order.order
            OrderMasterService.sync_order_started(order)
            RobotProfile.objects.filter(pk=sub_order.amr_id).update(
                is_used_by_master=sub_order.order_master
            )
            _forget_anchor(key)
            with _dispatched_lock:
                _dispatched_in_flight.discard(key)
            self.logger.info(
                "AMR %s đã nhận đơn %s", sub_order.amr_id, order.order_id
            )
            return

        now = timezone.now()
        sent_at = sub_order.sent_at or now
        if (now - sent_at).total_seconds() > SENT_TIMEOUT_SECONDS:
            OrderMasterService.fail_order(
                sub_order.order,
                f"Order not accepted within {int(SENT_TIMEOUT_SECONDS)}s",
            )
            _forget_anchor(key)
            with _dispatched_lock:
                _dispatched_in_flight.discard(key)
            return

        last_sent = sub_order.last_sent_at or sent_at
        if (now - last_sent).total_seconds() >= RESEND_INTERVAL_SECONDS:
            self._resend(sub_order)

    def _resend(self, sub_order: SubOrder) -> None:
        robot = sub_order.amr
        payload = build_sub_order_payload(sub_order)
        topic = order_topic(robot.manufacturer, robot.serial_number)
        self.publish(topic, payload, qos=settings.MQTT_QOS)
        SubOrder.objects.filter(pk=sub_order.pk).update(
            resend_count=F("resend_count") + 1,
            last_sent_at=timezone.now(),
        )
        _remember_anchor(
            str(sub_order.sub_order_id),
            sub_order.order_update_id,
            sub_order.anchor_seq or 0,
        )
        self.logger.info(
            "Gửi lại đơn %s cho %s (lần %s)",
            sub_order.order_id,
            robot.serial_number,
            sub_order.resend_count + 1,
        )


class SubOrderSentTimeoutChecker(WorkerThread):
    """Quét SubOrder kẹt ở trạng thái SENT quá lâu và đánh dấu thất bại."""

    def __init__(self, thread_name="suborder_sent_timeout",
                 worker_name="suborder_sent_timeout", loop_sleep_s=2.0):
        super().__init__(
            thread_name=thread_name, worker_name=worker_name, loop_sleep_s=loop_sleep_s
        )

    def task(self) -> None:
        cutoff = timezone.now() - timezone.timedelta(seconds=SENT_TIMEOUT_SECONDS)
        stuck = (
            SubOrder.objects.filter(status="SENT", sent_at__lt=cutoff)
            .select_related("order")
            .order_by("sent_at")[:20]
        )
        for sub_order in stuck:
            reason = (
                "Stuck in SENT execution loop state "
                f"(>{int(SENT_TIMEOUT_SECONDS)}s)"
            )
            if sub_order.order is not None:
                OrderMasterService.fail_order(sub_order.order, reason)
            else:
                sub_order.mark_failed(reason)
            _forget_anchor(str(sub_order.sub_order_id))
            with _dispatched_lock:
                _dispatched_in_flight.discard(str(sub_order.sub_order_id))
            self.logger.warning("SubOrder %s timeout: %s", sub_order.sub_order_id, reason)


