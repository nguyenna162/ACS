import uuid

from rest_framework import serializers

from acs.models import (
    ActionDispatch,
    Order,
    OrderAction,
    OrderMaster,
    OrderNode,
    SubOrder,
    SupplyRequest,
    SystemLog,
)
from buffer.serializers import BufferSlotSerializer

class OrderActionSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderAction
        fields = ['action_id', 'action_type', 'blocking_type', 'action_status', 'params']

class OrderNodeSerializer(serializers.ModelSerializer):
    actions = OrderActionSerializer(many=True, read_only=True)
    pos_x = serializers.FloatField(source='node.pos_x', read_only=True)
    pos_y = serializers.FloatField(source='node.pos_y', read_only=True)

    class Meta:
        model = OrderNode
        fields = ['id', 'node_id', 'pos_x', 'pos_y', 'sequence_id', 'released', 'node_status', 'actions']

class OrderSerializer(serializers.ModelSerializer):
    order_nodes = OrderNodeSerializer(many=True, read_only=True)
    robot_serial = serializers.CharField(source='robot.serial_number', read_only=True)

    class Meta:
        model = Order
        fields = [
            'order_id', 'order_type', 'order_master', 'robot', 'robot_serial',
            'src_slot', 'dst_slot', 'order_update_id', 'order_status', 'priority',
            'queued_at', 'started_at', 'failure_reason',
            'created_at', 'completed_at', 'order_nodes',
        ]

class OrderCreateSerializer(serializers.Serializer):
    order_id = serializers.CharField(max_length=64, required=False, allow_blank=True)
    src_slot = serializers.CharField(max_length=64)
    dst_slot = serializers.CharField(max_length=64)
    priority = serializers.IntegerField(default=1, required=False)
    order_type = serializers.ChoiceField(
        choices=[choice[0] for choice in Order.ORDER_TYPES], default='TRANSPORT', required=False
    )

    def validate_order_id(self, value):
        if not value:
            return f"ORD_{uuid.uuid4().hex[:8].upper()}"
        return value

class InstantActionSerializer(serializers.Serializer):
    serial_number = serializers.CharField(max_length=64)
    action_type = serializers.ChoiceField(choices=['startPause', 'stopPause', 'cancelOrder'])


class SupplyRequestSerializer(serializers.ModelSerializer):
    src_slot_detail = BufferSlotSerializer(source='src_slot', read_only=True)
    dst_slot_detail = BufferSlotSerializer(source='dst_slot', read_only=True)

    class Meta:
        model = SupplyRequest
        fields = [
            'id', 'request_id', 'source', 'line_id', 'process', 'product_model',
            'quantity', 'priority', 'sla_deadline', 'status',
            'src_slot', 'dst_slot', 'src_slot_detail', 'dst_slot_detail',
            'amr', 'order_master', 'payload', 'outcome',
            'created_at', 'updated_at', 'completed_at',
        ]
        read_only_fields = [
            'id', 'status', 'src_slot', 'dst_slot', 'amr', 'order_master',
            'outcome', 'created_at', 'updated_at', 'completed_at',
        ]


class SupplyRequestCreateSerializer(serializers.Serializer):
    request_id = serializers.CharField(max_length=64, required=False, allow_blank=True)
    source = serializers.ChoiceField(
        choices=[choice[0] for choice in SupplyRequest.SOURCE_CHOICES],
        default='MANUAL', required=False,
    )
    line_id = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    process = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    product_model = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    quantity = serializers.IntegerField(min_value=1, default=1, required=False)
    priority = serializers.IntegerField(default=50, required=False)
    sla_deadline = serializers.DateTimeField(required=False, allow_null=True)
    src_slot = serializers.CharField(max_length=64, required=False, allow_blank=True)
    dst_slot = serializers.CharField(max_length=64, required=False, allow_blank=True)
    payload = serializers.JSONField(required=False, default=dict)
    auto_pair = serializers.BooleanField(default=True, required=False)


class ActionDispatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActionDispatch
        fields = [
            'action_id', 'sub_order', 'order', 'amr', 'action_type',
            'blocking_type', 'status', 'parameters',
            'created_at', 'updated_at', 'finished_at',
        ]


class SubOrderSerializer(serializers.ModelSerializer):
    action_dispatches = serializers.SerializerMethodField()
    amr_serial = serializers.CharField(source='amr.serial_number', read_only=True)
    order_status = serializers.CharField(source='order.order_status', read_only=True)
    master_id = serializers.CharField(source='order_master.master_id', read_only=True)

    class Meta:
        model = SubOrder
        fields = [
            'id', 'sub_order_id', 'order_master', 'master_id', 'order', 'order_status',
            'amr', 'amr_serial', 'status', 'priority', 'path', 'anchor_seq',
            'order_update_id', 'resend_count', 'outcome',
            'sent_at', 'last_sent_at', 'acked_at', 'completed_at',
            'created_at', 'updated_at', 'action_dispatches',
        ]

    def get_action_dispatches(self, obj):
        return ActionDispatchSerializer(obj.action_dispatches.all(), many=True).data


class OrderMasterSerializer(serializers.ModelSerializer):
    sub_orders = SubOrderSerializer(many=True, read_only=True)
    supply_request_detail = SupplyRequestSerializer(source='supply_request', read_only=True)

    class Meta:
        model = OrderMaster
        fields = [
            'id', 'master_id', 'supply_request', 'supply_request_detail',
            'status', 'priority', 'plan', 'outcome',
            'created_at', 'started_at', 'completed_at', 'sub_orders',
        ]
        read_only_fields = ['id', 'created_at', 'started_at', 'completed_at']


class SystemLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = SystemLog
        fields = [
            'id', 'category', 'level', 'message', 'actor',
            'robot', 'order', 'sub_order', 'payload', 'created_at',
        ]
        read_only_fields = fields


