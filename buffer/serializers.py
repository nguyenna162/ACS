from rest_framework import serializers
from buffer.models import (
    ActionOption,
    BufferSlot,
    BufferZone,
    Schedule,
    ScheduleStop,
    StopAction,
)

class BufferSlotSerializer(serializers.ModelSerializer):
    node_id = serializers.CharField(source='node.node_id', read_only=True)

    class Meta:
        model = BufferSlot
        fields = [
            'slot_id', 'zone', 'node', 'node_id', 'name', 'rssid', 'buffer_type',
            'product_model', 'process', 'role_id', 'slot_status', 'enable',
            'ip_address', 'pallet_code', 'handle_order', 'last_success_transfer',
            'time_gap_btw_transfer', 'data', 'updated_at',
        ]

class BufferZoneSerializer(serializers.ModelSerializer):
    slots = BufferSlotSerializer(many=True, read_only=True)

    class Meta:
        model = BufferZone
        fields = ['zone_id', 'map_topology', 'zone_name', 'zone_type', 'capacity', 'slots']


class ActionOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActionOption
        fields = ['id', 'name', 'action_type', 'description']
        read_only_fields = ['id']


class StopActionSerializer(serializers.ModelSerializer):
    class Meta:
        model = StopAction
        fields = ['id', 'schedule_stop', 'action', 'action_option', 'sequence']
        read_only_fields = ['id']


class ScheduleStopSerializer(serializers.ModelSerializer):
    stop_actions = StopActionSerializer(many=True, read_only=True)

    class Meta:
        model = ScheduleStop
        fields = ['id', 'schedule', 'buffer', 'role_id', 'sequence', 'stop_actions']
        read_only_fields = ['id']


class ScheduleSerializer(serializers.ModelSerializer):
    stops = ScheduleStopSerializer(many=True, read_only=True)

    class Meta:
        model = Schedule
        fields = [
            'id', 'name', 'description', 'map', 'number_of_amr_need',
            'number_of_active_order', 'enable', 'created_at', 'updated_at', 'stops',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

