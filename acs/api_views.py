"""REST API điều phối ACS: đơn hàng, cấp vật tư, OrderMaster/SubOrder, log."""

import logging

from django.conf import settings
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from acs.dispatcher import OrderDispatcher
from acs.models import (
    ActionDispatch,
    Order,
    OrderMaster,
    SubOrder,
    SupplyRequest,
    SystemLog,
)
from acs.serializers import (
    ActionDispatchSerializer,
    InstantActionSerializer,
    OrderCreateSerializer,
    OrderMasterSerializer,
    OrderSerializer,
    SubOrderSerializer,
    SupplyRequestCreateSerializer,
    SupplyRequestSerializer,
    SystemLogSerializer,
)
from acs.services import OrderMasterService, SupplyService
from acs.vda5050 import build_instant_action_payload, build_order_payload
from workers.acs_system import TOPIC_ORDER, EventBus
from workers.acs_system import instant_actions_topic, next_header_id, order_topic
from workers.acs_system import MqttOutbox
from amr.models import RobotProfile
from traffic.fleet_scheduler import FleetScheduler
from traffic.models import FleetTask, RobotSnapshot

logger = logging.getLogger("acs.views")


def enqueue_instant_action(robot: RobotProfile, action_type: str, action_id: str) -> str:
    payload = build_instant_action_payload(robot, action_type, action_id)
    topic = instant_actions_topic(robot.manufacturer, robot.serial_number)
    MqttOutbox.enqueue(topic, payload, qos=settings.MQTT_QOS)
    return topic


class OrderViewSet(viewsets.ModelViewSet):
    queryset = (
        Order.objects.select_related("robot", "src_slot", "dst_slot")
        .all()
        .order_by("-created_at")
    )
    serializer_class = OrderSerializer
    filterset_fields = ["order_status", "robot", "order_type", "order_master"]

    def create(self, request, *args, **kwargs):
        serializer = OrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        max_queue = int(getattr(settings, "ACS_ORDER_MAX_QUEUE", 500))
        if Order.objects.filter(order_status__in=["CREATED", "ASSIGNED"]).count() >= max_queue:
            return Response(
                {"error": "Hàng đợi đơn đã đầy, vui lòng thử lại sau."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        order_id = data.get("order_id") or f"ORD_{timezone.now():%y%m%d%H%M%S%f}"[:28]
        try:
            order = OrderDispatcher.create_and_dispatch_order(
                order_id=order_id,
                src_slot_id=data["src_slot"],
                dst_slot_id=data["dst_slot"],
                priority=data.get("priority", 1),
                order_type=data.get("order_type", "TRANSPORT"),
            )
        except (ValueError, RuntimeError) as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        topic = order_topic(order.robot.manufacturer, order.robot.serial_number)
        payload = build_order_payload(
            order, header_id=next_header_id(topic)
        )
        MqttOutbox.enqueue(topic, payload, qos=settings.MQTT_QOS)

        result_data = OrderSerializer(order).data
        EventBus.publish(
            TOPIC_ORDER,
            {"event": "order_created", "order_id": order.order_id, "amr": order.robot_id},
        )
        return Response(result_data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        order = self.get_object()
        cancelled_order = OrderDispatcher.cancel_order(order.order_id)
        if cancelled_order.robot_id:
            enqueue_instant_action(
                cancelled_order.robot, "cancelOrder", f"ACT_CANCEL_{cancelled_order.order_id[-8:]}"
            )
        EventBus.publish(
            TOPIC_ORDER,
            {"event": "order_cancelled", "order_id": cancelled_order.order_id},
        )
        return Response(OrderSerializer(cancelled_order).data)

    @action(detail=True, methods=["post"])
    def resend(self, request, pk=None):
        order = self.get_object()
        if order.is_terminal or not order.robot_id:
            return Response(
                {"error": "Đơn đã kết thúc hoặc chưa gán robot."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        topic = order_topic(order.robot.manufacturer, order.robot.serial_number)
        payload = build_order_payload(
            order, header_id=next_header_id(topic)
        )
        MqttOutbox.enqueue(topic, payload, qos=settings.MQTT_QOS)
        return Response({"status": "queued", "topic": topic})


class InstantActionView(APIView):
    throttle_scope = "control"

    def post(self, request):
        serializer = InstantActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        robot = RobotProfile.objects.filter(pk=data["serial_number"]).first()
        if robot is None:
            return Response(
                {"error": f"Robot {data['serial_number']} not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        action_id = f"ACT_{data['action_type'].upper()}_{timezone.now():%H%M%S%f}"
        topic = enqueue_instant_action(robot, data["action_type"], action_id)
        return Response(
            {
                # Giữ nguyên giá trị "published" cho HMI hiện hữu; bản tin được
                # bảo đảm phát qua Transactional Outbox ("delivery": "outbox").
                "status": "published",
                "delivery": "outbox",
                "topic": topic,
                "action_id": action_id,
                "action_type": data["action_type"],
            },
            status=status.HTTP_200_OK,
        )


class SupplyRequestViewSet(viewsets.ModelViewSet):
    queryset = SupplyRequest.objects.select_related(
        "src_slot", "dst_slot", "amr", "order_master"
    ).all()
    serializer_class = SupplyRequestSerializer
    filterset_fields = ["status", "source", "line_id", "amr"]
    search_fields = ["request_id", "line_id", "product_model"]

    def create(self, request, *args, **kwargs):
        serializer = SupplyRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        supply_request = SupplyService.create_request(
            request_id=data.get("request_id") or None,
            source=data["source"],
            line_id=data["line_id"],
            process=data["process"],
            product_model=data["product_model"],
            quantity=data["quantity"],
            priority=data["priority"],
            sla_deadline=data.get("sla_deadline"),
            src_slot_id=data.get("src_slot") or None,
            dst_slot_id=data.get("dst_slot") or None,
            payload=data.get("payload") or {},
            auto_pair=data["auto_pair"],
        )
        supply_request.refresh_from_db()
        return Response(
            SupplyRequestSerializer(supply_request).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["post"])
    def pair(self, request):
        limit = request.data.get("limit")
        try:
            limit = int(limit) if limit is not None else None
        except (TypeError, ValueError):
            limit = None
        return Response(SupplyService.pair_pending(limit))

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        supply_request = self.get_object()
        if supply_request.status in ("COMPLETED", "CANCELLED"):
            return Response(SupplyRequestSerializer(supply_request).data)
        supply_request.status = "CANCELLED"
        supply_request.outcome = str(
            request.data.get("reason", "Cancelled by operator")
        )[:255]
        supply_request.save(update_fields=["status", "outcome", "updated_at"])
        return Response(SupplyRequestSerializer(supply_request).data)


class OrderMasterViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (
        OrderMaster.objects.select_related("supply_request")
        .prefetch_related("sub_orders__action_dispatches", "orders")
        .all()
    )
    serializer_class = OrderMasterSerializer
    filterset_fields = ["status", "priority"]
    search_fields = ["master_id", "supply_request__request_id"]

    @action(detail=True, methods=["post"])
    def plan(self, request, pk=None):
        master = self.get_object()
        result = OrderMasterService.plan_master(master)
        master.refresh_from_db()
        return Response({"result": result, "master": OrderMasterSerializer(master).data})

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        master = self.get_object()
        if master.is_terminal:
            return Response(OrderMasterSerializer(master).data)

        sub_orders = master.sub_orders.exclude(
            status__in=["COMPLETED", "FAILED", "CANCELLED"]
        ).select_related("order", "amr")
        for sub_order in sub_orders:
            if sub_order.order_id and sub_order.order and not sub_order.order.is_terminal:
                OrderDispatcher.cancel_order(sub_order.order_id)
                if sub_order.amr_id:
                    enqueue_instant_action(
                        sub_order.amr,
                        "cancelOrder",
                        f"ACT_CANCEL_{str(sub_order.sub_order_id)[:8]}",
                    )
            sub_order.status = "CANCELLED"
            sub_order.completed_at = timezone.now()
            sub_order.save(update_fields=["status", "completed_at", "updated_at"])

        master.status = "CANCELLED"
        master.outcome = str(request.data.get("reason", "Cancelled by operator"))[:255]
        master.completed_at = timezone.now()
        master.save(update_fields=["status", "outcome", "completed_at"])
        if master.supply_request_id:
            SupplyRequest.objects.filter(pk=master.supply_request_id).update(
                status="CANCELLED", outcome=master.outcome
            )
        EventBus.publish(
            TOPIC_ORDER, {"event": "master_cancelled", "master_id": master.master_id}
        )
        return Response(OrderMasterSerializer(master).data)


class SubOrderViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (
        SubOrder.objects.select_related("order", "amr", "order_master")
        .prefetch_related("action_dispatches")
        .all()
    )
    serializer_class = SubOrderSerializer
    filterset_fields = ["status", "amr", "order_master"]

    @action(detail=True, methods=["post"])
    def resend(self, request, pk=None):
        sub_order = self.get_object()
        if sub_order.status not in ("PENDING", "SENT", "ACKED") or sub_order.order is None:
            return Response(
                {"error": "Chỉ gửi lại được sub-order đang chờ xác nhận."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        topic = order_topic(sub_order.amr.manufacturer, sub_order.amr.serial_number)
        payload = build_order_payload(
            sub_order.order, header_id=next_header_id(topic)
        )
        MqttOutbox.enqueue(topic, payload, qos=settings.MQTT_QOS)
        SubOrder.objects.filter(pk=sub_order.pk).update(
            last_sent_at=timezone.now(), resend_count=sub_order.resend_count + 1
        )
        return Response({"status": "queued", "topic": topic})


class ActionDispatchViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ActionDispatch.objects.select_related("amr", "order", "sub_order").all()
    serializer_class = ActionDispatchSerializer
    filterset_fields = ["status", "amr", "action_type"]


class SystemLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SystemLog.objects.all()
    serializer_class = SystemLogSerializer
    filterset_fields = ["category", "level", "robot", "order"]
    search_fields = ["message", "actor"]


class FleetScheduleView(APIView):
    """Xem trước phương án gán nhiệm vụ (Hungarian) mà không thay đổi dữ liệu."""

    def post(self, request):
        from django.utils.dateparse import parse_datetime

        tasks_payload = request.data.get("tasks") or []
        robots_payload = request.data.get("robots") or []
        if not isinstance(tasks_payload, list) or not isinstance(robots_payload, list):
            return Response(
                {"error": "tasks và robots phải là danh sách."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tasks = [
            FleetTask(
                task_id=str(item.get("task_id") or index),
                start_node=str(item.get("start_node", "")),
                end_node=str(item.get("end_node", item.get("start_node", ""))),
                priority=int(item.get("priority", 50) or 50),
                task_type=str(item.get("task_type", "TRANSFER")),
                sla_deadline=parse_datetime(item["sla_deadline"])
                if item.get("sla_deadline")
                else None,
                payload=item.get("payload") or {},
            )
            for index, item in enumerate(tasks_payload)
            if item.get("start_node")
        ]
        robots = [
            RobotSnapshot(
                serial_number=str(item.get("serial_number")),
                current_node=item.get("current_node"),
                battery_level=float(item.get("battery_level", 100.0) or 0.0),
                operational_state=str(item.get("operational_state", "IDLE")),
                is_idle=bool(item.get("is_idle", True)),
            )
            for item in robots_payload
            if item.get("serial_number")
        ]

        scheduler = FleetScheduler()
        strategy = str(request.data.get("strategy", "tiered")).lower()
        if strategy == "global":
            result = scheduler.schedule_global(tasks, robots)
        else:
            result = scheduler.schedule(tasks, robots)
        return Response(result.as_dict())


