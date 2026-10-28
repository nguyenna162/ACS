from rest_framework import serializers

from amr.models import RobotProfile, RobotTelemetry, TopicControl


class RobotTelemetrySerializer(serializers.ModelSerializer):
    class Meta:
        model = RobotTelemetry
        fields = [
            "id", "order_id", "order_update_id", "operating_mode",
            "velocity_vx", "node_states", "edge_states", "action_state", "loads",
            "errors", "information", "amr_position", "velocity", "battery_state",
            "safety_state", "driving", "paused", "new_base_request",
            "error_codes", "recorded_at",
        ]


class TopicControlSerializer(serializers.ModelSerializer):
    class Meta:
        model = TopicControl
        fields = [
            "id", "topic", "topic_type", "amr", "manufacturer", "version",
            "header_id", "last_call",
        ]
        read_only_fields = ["header_id", "last_call"]


class RobotProfileSerializer(serializers.ModelSerializer):
    recent_telemetry = serializers.SerializerMethodField()

    class Meta:
        model = RobotProfile
        fields = [
            "serial_number", "manufacturer", "amr_model", "interface", "version",
            "ip_address", "port", "current_node", "last_node_id",
            "battery_level", "operational_state", "pos_x", "pos_y", "theta",
            "order_id", "order_update_id", "last_state_seq",
            "operating_mode", "driving", "paused", "new_base_request",
            "zone_set_id", "is_online", "is_idle", "is_used_by_master",
            "errors", "safety_state", "battery_state", "amr_position", "velocity",
            "node_states", "edge_states", "action_state", "loads", "information",
            "last_heartbeat", "state_received_at", "recent_telemetry",
        ]
        read_only_fields = [
            "order_id", "order_update_id", "last_state_seq", "is_used_by_master",
            "last_heartbeat", "state_received_at",
        ]

    def get_recent_telemetry(self, obj):
        latest = obj.telemetries.order_by("-recorded_at").first()
        if latest:
            return RobotTelemetrySerializer(latest).data
        return None

