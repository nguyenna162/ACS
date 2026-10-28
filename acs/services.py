"""Dịch vụ nghiệp vụ ACS: yêu cầu cấp vật tư, ghép cặp, kế hoạch đơn tổng.

Luồng theo tài liệu::

    SupplyRequest (Aligo/manual)
        -> ghép cặp ưu tiên + Dijkstra + SLA (FleetScheduler)
        -> OrderMaster
        -> SubOrder (1 AMR/chặng)
        -> Order (VDA 5050, do OrderDispatcher tạo)
        -> SubOrderDispatcher phát MQTT, OrderSendMqtt theo dõi ack/resend
"""

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from acs.dispatcher import OrderDispatcher
from acs.models import (
    MasterIdSequence,
    Order,
    OrderMaster,
    SubOrder,
    SupplyRequest,
    SystemLog,
)
from buffer.models import BufferSlot
from workers.acs_system import TOPIC_ALERT, TOPIC_ORDER, EventBus
from workers.acs_system import PAIRING_LOCK_KEY, acquire_advisory_xact_lock
from amr.models import RobotProfile
from traffic.fleet_scheduler import FleetScheduler
from traffic.graph_distance import shortest_path
from traffic.models import FleetTask, RobotPriority, RobotSnapshot

logger = logging.getLogger("acs.services")


class SupplyService:
    """Tạo và ghép yêu cầu cấp vật tư với AMR khả dụng."""

    # -- tạo yêu cầu ------------------------------------------------------
    @staticmethod
    def create_request(
        request_id: str | None = None,
        source: str = "MANUAL",
        line_id: str = "",
        process: str = "",
        product_model: str = "",
        quantity: int = 1,
        priority: int = int(RobotPriority.NORMAL),
        sla_deadline=None,
        src_slot_id: str | None = None,
        dst_slot_id: str | None = None,
        payload: dict | None = None,
        auto_pair: bool = True,
    ) -> SupplyRequest:
        if not request_id:
            stamp = timezone.now()
            request_id = f"SR-{stamp:%Y%m%d%H%M%S}-{stamp.microsecond // 1000:03d}"
        request, created = SupplyRequest.objects.get_or_create(
            request_id=request_id,
            defaults={
                "source": source,
                "line_id": line_id,
                "process": process,
                "product_model": product_model,
                "quantity": max(1, int(quantity)),
                "priority": int(priority),
                "sla_deadline": sla_deadline,
                "src_slot_id": src_slot_id,
                "dst_slot_id": dst_slot_id,
                "payload": payload or {},
            },
        )
        if created:
            EventBus.publish(
                TOPIC_ORDER,
                {"event": "supply_request_created", "request_id": request.request_id},
            )
            if auto_pair:
                SupplyService.pair_pending()
        return request

    # -- chọn ô nguồn/đích -----------------------------------------------
    @staticmethod
    def resolve_slots(request: SupplyRequest, exclude_src: set | None = None,
                      exclude_dst: set | None = None) -> tuple[BufferSlot, BufferSlot]:
        exclude_src = exclude_src or set()
        exclude_dst = exclude_dst or set()

        src = None
        if request.src_slot_id:
            src = BufferSlot.objects.filter(pk=request.src_slot_id, enable=True).first()
        if src is None:
            sources = BufferSlot.objects.filter(
                enable=True, slot_status="OCCUPIED"
            ).exclude(pk__in=exclude_src)
            if request.product_model:
                sources = sources.filter(product_model=request.product_model)
            src = sources.order_by("slot_id").first()
        if src is None:
            raise ValueError("Không tìm thấy ô nguồn có hàng để cấp vật tư.")

        dst = None
        if request.dst_slot_id:
            dst = BufferSlot.objects.filter(pk=request.dst_slot_id, enable=True).first()
        if dst is None:
            targets = BufferSlot.objects.filter(
                enable=True, slot_status="EMPTY"
            ).exclude(pk__in=exclude_dst).exclude(pk=src.pk)
            if request.product_model:
                matched = (
                    targets.filter(product_model=request.product_model)
                    .order_by("slot_id").first()
                )
                dst = matched or targets.order_by("slot_id").first()
            else:
                dst = targets.filter(
                    buffer_type__in=["LINE", "DROP", "STORAGE"]
                ).order_by("slot_id").first()
        if dst is None:
            raise ValueError("Không tìm thấy ô đích trống để nhận vật tư.")

        _distance, path = shortest_path(src.node_id, dst.node_id)
        if not path:
            raise ValueError(f"Không có lộ trình từ {src.node_id} tới {dst.node_id}.")
        return src, dst

    # -- ghép cặp ---------------------------------------------------------
    @staticmethod
    def _robot_snapshots() -> list[RobotSnapshot]:
        min_battery = float(getattr(settings, "ACS_MIN_BATTERY_PERCENT", 25.0))
        robots = RobotProfile.objects.exclude(
            operational_state__in=["OFFLINE", "ERROR", "INIT"]
        ).filter(battery_level__gte=min_battery)
        return [robot.as_snapshot() for robot in robots]

    @staticmethod
    def pair_pending(limit: int | None = None) -> dict:
        limit = int(limit or getattr(settings, "ACS_PAIRING_BATCH_SIZE", 50))
        now = timezone.now()

        with transaction.atomic():
            acquire_advisory_xact_lock(PAIRING_LOCK_KEY)
            expired = SupplyRequest.objects.filter(
                status="PENDING", sla_deadline__lt=now - timezone.timedelta(minutes=5)
            )
            expired_count = expired.update(
                status="FAILED", outcome="Quá hạn SLA trước khi ghép cặp"
            )

            pending = list(
                SupplyRequest.objects.filter(status="PENDING")
                .order_by("-priority", "sla_deadline", "created_at")[:limit]
            )
            if not pending:
                return {"paired": 0, "unassigned": 0, "failed": 0, "expired": expired_count}

            robots = SupplyService._robot_snapshots()
            tasks: list[FleetTask] = []
            resolved: dict[str, tuple[SupplyRequest, BufferSlot, BufferSlot]] = {}
            used_src: set[str] = set()
            used_dst: set[str] = set()
            failed = 0

            for request in pending:
                try:
                    src, dst = SupplyService.resolve_slots(request, used_src, used_dst)
                except ValueError as exc:
                    request.status = "FAILED"
                    request.outcome = str(exc)[:255]
                    request.save(update_fields=["status", "outcome", "updated_at"])
                    failed += 1
                    continue
                used_src.add(src.pk)
                used_dst.add(dst.pk)
                resolved[str(request.pk)] = (request, src, dst)
                tasks.append(
                    FleetTask(
                        task_id=str(request.pk),
                        start_node=src.node_id,
                        end_node=dst.node_id,
                        priority=int(request.priority),
                        task_type="SUPPLY",
                        sla_deadline=request.sla_deadline,
                    )
                )

            if not tasks or not robots:
                return {
                    "paired": 0,
                    "unassigned": len(tasks),
                    "failed": failed,
                    "expired": expired_count,
                    "reason": "no_robot_available" if tasks else "no_task",
                }

            result = FleetScheduler().schedule(tasks, robots)
            paired = 0
            for assignment in result.assignments:
                if not assignment.assigned:
                    continue
                request, src, dst = resolved[assignment.task.task_id]
                if SupplyService._create_master_for(request, src, dst, assignment) is not None:
                    paired += 1

            return {
                "paired": paired,
                "unassigned": len(result.unassigned),
                "failed": failed,
                "expired": expired_count,
                "strategy": result.strategy,
            }

    @staticmethod
    def _create_master_for(request: SupplyRequest, src: BufferSlot, dst: BufferSlot,
                           assignment) -> OrderMaster | None:
        with transaction.atomic():
            request = SupplyRequest.objects.select_for_update().get(pk=request.pk)
            if request.status != "PENDING":
                return None
            _distance, nodes = shortest_path(src.node_id, dst.node_id)
            sequence = MasterIdSequence.next_value()
            master_id = f"MES-{timezone.localdate():%Y%m%d}-{sequence:04d}"
            master = OrderMaster.objects.create(
                master_id=master_id,
                supply_request=request,
                status="PLANNING",
                priority=request.priority,
                plan={
                    "strategy": "hungarian",
                    "src_slot": src.pk,
                    "dst_slot": dst.pk,
                    "amr": assignment.amr_serial_number,
                    "cost": assignment.cost,
                    "distance": assignment.distance,
                    "nodes": nodes,
                },
            )
            request.src_slot = src
            request.dst_slot = dst
            request.amr_id = assignment.amr_serial_number
            request.order_master = master
            request.status = "PAIRED"
            request.save(
                update_fields=[
                    "src_slot", "dst_slot", "amr", "order_master", "status", "updated_at",
                ]
            )
            SubOrder.objects.create(
                order_master=master,
                amr_id=assignment.amr_serial_number,
                status="PENDING",
                priority=request.priority,
                path={
                    "src_slot": src.pk,
                    "dst_slot": dst.pk,
                    "nodes": nodes,
                    "distance": None if assignment.distance == float("inf") else assignment.distance,
                    "zoneSetId": None,
                },
            )
            RobotProfile.objects.filter(pk=assignment.amr_serial_number).update(
                is_used_by_master=master
            )
            SystemLog.write(
                f"Ghép {request.request_id} -> {assignment.amr_serial_number} ({master_id})",
                category="ORDER",
                actor="supply_pairing",
                payload={
                    "src": src.pk,
                    "dst": dst.pk,
                    "cost": assignment.cost,
                    "distance": assignment.distance,
                },
            )
            EventBus.publish(
                TOPIC_ORDER,
                {
                    "event": "supply_paired",
                    "request_id": request.request_id,
                    "master_id": master.master_id,
                    "amr": assignment.amr_serial_number,
                },
            )
            return master


class OrderMasterService:
    """Lập kế hoạch đơn tổng thành các Order VDA 5050 và đồng bộ trạng thái."""

    @staticmethod
    def plan_master(master: OrderMaster, now=None) -> dict:
        now = now or timezone.now()
        if master.is_terminal:
            return {"skipped": True, "reason": "terminal"}
        pending_subs = list(
            master.sub_orders.exclude(status__in=["COMPLETED", "FAILED", "CANCELLED"])
        )
        if not pending_subs:
            master.refresh_status()
            return {"planned": 0, "reason": "no_pending_sub_order"}

        request = master.supply_request
        if request is None or not request.src_slot_id or not request.dst_slot_id:
            master.status = "FAILED"
            master.outcome = "Thiếu ô nguồn/đích cho kế hoạch"
            master.completed_at = now
            master.save(update_fields=["status", "outcome", "completed_at"])
            return {"planned": 0, "reason": "missing_slots"}

        timeout = float(getattr(settings, "ACS_ORDER_QUEUE_TIMEOUT_SECONDS", 600.0))
        planned = 0
        for sub in pending_subs:
            if sub.order_id:
                continue
            if not sub.amr_id:
                sub.mark_failed("Chưa gán AMR")
                continue
            if (now - sub.created_at).total_seconds() > timeout:
                sub.mark_failed(f"Quá {int(timeout)}s chưa gửi được đơn")
                continue
            try:
                order = OrderDispatcher.create_and_dispatch_order(
                    order_id=f"{master.master_id}-{sub.amr_id}",
                    src_slot_id=request.src_slot_id,
                    dst_slot_id=request.dst_slot_id,
                    priority=sub.priority,
                    robot_serial=sub.amr_id,
                    order_type="TRANSFER",
                    order_master=master,
                )
            except (ValueError, RuntimeError) as exc:
                sub.outcome = str(exc)[:255]
                sub.save(update_fields=["outcome", "updated_at"])
                logger.warning("Chưa thể tạo Order cho %s: %s", sub.amr_id, exc)
                continue

            sub.order = order
            sub.order_update_id = order.order_update_id
            nodes = list(
                order.order_nodes.order_by("sequence_id").values_list("node_id", flat=True)
            )
            sub.path = {**(sub.path or {}), "nodes": nodes, "order_id": order.order_id}
            sub.save(update_fields=["order", "order_update_id", "path", "updated_at"])
            planned += 1

        master.status = "PROCESSING"
        master.started_at = master.started_at or now
        master.save(update_fields=["status", "started_at"])
        if request:
            request.status = "IN_PROGRESS"
            request.save(update_fields=["status", "updated_at"])
        master.refresh_status()
        return {"planned": planned, "master_status": master.status}

    @staticmethod
    def plan_pending_masters(limit: int | None = None) -> int:
        limit = int(limit or getattr(settings, "ACS_ORDER_MASTER_POOL", 3))
        masters = OrderMaster.objects.filter(
            status__in=["NEW", "PLANNING", "PROCESSING"]
        ).order_by("created_at", "id")[:limit]
        planned = 0
        for master in masters:
            try:
                with transaction.atomic():
                    locked = OrderMaster.objects.select_for_update().get(pk=master.pk)
                    result = OrderMasterService.plan_master(locked)
                    planned += int(result.get("planned", 0))
            except OrderMaster.DoesNotExist:  # pragma: no cover
                continue
        return planned

    @staticmethod
    def sync_order_started(order: Order) -> None:
        """Đánh dấu Order/OrderMaster/SupplyRequest đã được AMR xác nhận."""
        now = timezone.now()
        updates = {"order_status": "EXECUTING"}
        if order.started_at is None:
            order.started_at = now
            updates["started_at"] = now
        Order.objects.filter(pk=order.pk).update(**updates)
        sub = SubOrder.objects.filter(order=order).select_related("order_master").first()
        if sub is not None:
            master = sub.order_master
            if master.status in ("NEW", "PLANNING"):
                master.status = "PROCESSING"
                master.started_at = master.started_at or now
                master.save(update_fields=["status", "started_at"])
            request = master.supply_request
            if request is not None and request.status == "PAIRED":
                request.status = "IN_PROGRESS"
                request.save(update_fields=["status", "updated_at"])

    @staticmethod
    def complete_from_order(order: Order) -> None:
        now = timezone.now()
        sub = (
            SubOrder.objects.filter(order=order)
            .select_related("order_master", "order_master__supply_request")
            .first()
        )
        if sub is None:
            return
        if sub.status != "COMPLETED":
            sub.status = "COMPLETED"
            sub.completed_at = now
            sub.outcome = ""
            sub.save(update_fields=["status", "completed_at", "outcome", "updated_at"])
        master = sub.order_master
        master.refresh_status()
        request = master.supply_request
        if master.status == "COMPLETED" and request is not None:
            request.status = "COMPLETED"
            request.completed_at = now
            request.save(update_fields=["status", "completed_at", "updated_at"])
        if order.robot_id:
            RobotProfile.objects.filter(pk=order.robot_id).update(
                is_idle=True, is_used_by_master=None
            )
        SystemLog.write(
            f"Hoàn thành đơn {order.order_id}",
            category="ORDER",
            actor="order_master",
            order=order,
            sub_order=sub,
        )
        EventBus.publish(
            TOPIC_ORDER,
            {
                "event": "order_completed",
                "order_id": order.order_id,
                "master_id": master.master_id,
                "amr": order.robot_id,
            },
        )

    @staticmethod
    def fail_order(order: Order, reason: str) -> None:
        from map.traffic_lock import TrafficController

        now = timezone.now()
        Order.objects.filter(pk=order.pk).update(
            order_status="FAILED",
            failure_reason=(reason or "")[:255],
            completed_at=now,
        )
        sub = SubOrder.objects.filter(order=order).select_related("order_master").first()
        master = sub.order_master if sub else None
        if sub is not None and not sub.is_terminal:
            sub.mark_failed(reason)
        if master is not None:
            master.status = "FAILED"
            master.outcome = (reason or "")[:255]
            master.completed_at = now
            master.save(update_fields=["status", "outcome", "completed_at"])
            request = master.supply_request
            if request is not None:
                request.status = "FAILED"
                request.outcome = (reason or "")[:255]
                request.save(update_fields=["status", "outcome", "updated_at"])
        if order.robot_id:
            RobotProfile.objects.filter(pk=order.robot_id).update(
                is_idle=True, is_used_by_master=None
            )
        TrafficController.release_order_locks(order)
        SystemLog.write(
            f"Đơn {order.order_id} thất bại: {reason}",
            category="ORDER",
            level="ERROR",
            actor="order_master",
            order=order,
            sub_order=sub,
        )
        EventBus.publish(
            TOPIC_ALERT,
            {"event": "order_failed", "order_id": order.order_id, "reason": reason},
        )

    @staticmethod
    def sync_sub_orders() -> int:
        updated = 0
        queryset = (
            SubOrder.objects.exclude(status__in=["COMPLETED", "FAILED", "CANCELLED"])
            .exclude(order__isnull=True)
            .select_related("order")
        )
        for sub in queryset:
            order = sub.order
            if order.order_status == "COMPLETED":
                OrderMasterService.complete_from_order(order)
                updated += 1
            elif order.order_status in ("FAILED", "CANCELLED"):
                reason = order.failure_reason or order.order_status
                OrderMasterService.fail_order(order, reason)
                updated += 1
            elif order.order_status == "EXECUTING" and sub.status in ("SENT", "ACKED"):
                sub.status = "EXECUTING"
                sub.save(update_fields=["status", "updated_at"])
                updated += 1
        return updated




