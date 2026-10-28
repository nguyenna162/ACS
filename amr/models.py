"""Model đội xe AMR theo tài liệu dự án (module ``amr``).

``RobotProfile`` tương ứng ``AmrVehicle`` trong tài liệu và lưu cả trạng thái
VDA 5050 mới nhất (nodeStates, edgeStates, actionStates, loads, errors...) để
HMI hiển thị tức thời mà không phải đọc bảng log.

``RobotTelemetry`` (alias ``AmrStateLog``) là nhật ký trạng thái phục vụ audit.
"""

from django.db import models
from django.utils import timezone


class RobotProfile(models.Model):
    STATE_CHOICES = [
        ("INIT", "Initializing"),
        ("IDLE", "Idle"),
        ("RUNNING", "Running"),
        ("WARNING", "Warning"),
        ("BLOCKED", "Blocked by Traffic"),
        ("ERROR", "Hardware Error"),
        ("OFFLINE", "Offline / Connection Broken"),
    ]
    OPERATING_MODES = [
        ("AUTOMATIC", "Automatic"),
        ("SEMIAUTOMATIC", "Semi-automatic"),
        ("INTERVENED", "Intervened"),
        ("MANUAL", "Manual"),
        ("SERVICE", "Service"),
    ]

    serial_number = models.CharField(max_length=64, primary_key=True)
    manufacturer = models.CharField(max_length=64, default="SEVT_Robotics")
    amr_model = models.CharField(max_length=64, blank=True, default="")
    interface = models.CharField(max_length=32, blank=True, default="VDA5050")
    version = models.CharField(max_length=32, blank=True, default="2.0.0")
    ip_address = models.GenericIPAddressField(protocol="IPv4", null=True, blank=True)
    port = models.PositiveIntegerField(default=1883)

    current_node = models.ForeignKey(
        "map.Node", on_delete=models.SET_NULL, null=True, blank=True, related_name="robots_at_node"
    )
    last_node_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    battery_level = models.FloatField(default=100.0)
    operational_state = models.CharField(max_length=20, choices=STATE_CHOICES, default="INIT")
    pos_x = models.FloatField(default=0.0)
    pos_y = models.FloatField(default=0.0)
    theta = models.FloatField(default=0.0)

    # --- Trạng thái VDA 5050 mới nhất -------------------------------------
    order_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    order_update_id = models.BigIntegerField(default=0)
    last_state_seq = models.BigIntegerField(default=0)
    node_states = models.JSONField(default=list, blank=True)
    edge_states = models.JSONField(default=list, blank=True)
    action_state = models.JSONField(default=list, blank=True)
    loads = models.JSONField(default=list, blank=True)
    errors = models.JSONField(default=list, blank=True)
    information = models.JSONField(default=list, blank=True)
    amr_position = models.JSONField(default=dict, blank=True)
    velocity = models.JSONField(default=dict, blank=True)
    battery_state = models.JSONField(default=dict, blank=True)
    safety_state = models.JSONField(default=dict, blank=True)
    driving = models.BooleanField(default=False)
    paused = models.BooleanField(default=False)
    new_base_request = models.BooleanField(default=False)
    operating_mode = models.CharField(
        max_length=20, choices=OPERATING_MODES, default="AUTOMATIC"
    )
    zone_set_id = models.CharField(max_length=64, blank=True, default="")

    # --- Trạng thái vận hành nội bộ ---------------------------------------
    is_online = models.BooleanField(default=False)
    is_idle = models.BooleanField(default=True)
    is_used_by_master = models.ForeignKey(
        "acs.OrderMaster",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="robots",
    )
    last_heartbeat = models.DateTimeField(default=timezone.now, db_index=True)
    state_received_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "robot_profile"
        ordering = ["serial_number"]
        indexes = [
            models.Index(fields=["operational_state", "last_heartbeat"], name="robot_state_hb_idx"),
            models.Index(fields=["is_idle", "is_online"], name="robot_idle_online_idx"),
            models.Index(fields=["order_id"], name="robot_order_idx"),
        ]

    def __str__(self):
        return f"{self.serial_number} ({self.operational_state}, {self.battery_level:.1f}%)"

    # -- tiện ích ---------------------------------------------------------
    def is_stale(self, now=None, threshold_seconds: float = 10.0) -> bool:
        now = now or timezone.now()
        if not self.last_heartbeat:
            return True
        return (now - self.last_heartbeat).total_seconds() > threshold_seconds

    def mark_offline(self, now=None) -> bool:
        if self.operational_state == "OFFLINE":
            return False
        self.operational_state = "OFFLINE"
        self.is_online = False
        self.is_idle = False
        self.driving = False
        self.save(update_fields=["operational_state", "is_online", "is_idle", "driving"])
        return True

    def as_snapshot(self):
        """Chuyển sang RobotSnapshot cho bộ lập lịch (không truy vấn DB)."""
        from traffic.models import RobotSnapshot

        return RobotSnapshot(
            serial_number=self.serial_number,
            current_node=self.current_node_id or (self.last_node_id or None),
            battery_level=float(self.battery_level or 0.0),
            operational_state=self.operational_state,
            is_idle=bool(self.is_idle),
        )


class TopicControl(models.Model):
    """Cấu hình topic MQTT của từng AMR (theo dõi headerId/last_call)."""

    TOPIC_TYPES = [
        ("STATE", "State"),
        ("CONNECTION", "Connection"),
        ("ORDER", "Order"),
        ("INSTANT_ACTIONS", "Instant Actions"),
        ("FACTSHEET", "Factsheet"),
    ]

    id = models.BigAutoField(primary_key=True)
    topic = models.CharField(max_length=255, unique=True)
    topic_type = models.CharField(max_length=20, choices=TOPIC_TYPES, default="STATE")
    amr = models.ForeignKey(
        RobotProfile, on_delete=models.CASCADE, null=True, blank=True, related_name="topics"
    )
    manufacturer = models.CharField(max_length=64, blank=True, default="")
    version = models.CharField(max_length=32, blank=True, default="2.0.0")
    header_id = models.PositiveBigIntegerField(default=0)
    last_call = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "amr_topic_control"
        ordering = ["topic"]
        indexes = [models.Index(fields=["amr", "topic_type"], name="topic_amr_type_idx")]

    def __str__(self):
        return f"{self.topic} [{self.topic_type}]"

    def next_header_id(self) -> int:
        TopicControl.objects.filter(pk=self.pk).update(header_id=models.F("header_id") + 1)
        self.refresh_from_db(fields=["header_id"])
        return int(self.header_id)


class RobotTelemetry(models.Model):
    """Nhật ký trạng thái AMR (AmrStateLog) - phục vụ truy vết và thống kê."""

    id = models.BigAutoField(primary_key=True)
    robot = models.ForeignKey(RobotProfile, on_delete=models.CASCADE, related_name="telemetries")
    order_id = models.CharField(max_length=64, blank=True, default="")
    order_update_id = models.BigIntegerField(default=0)
    operating_mode = models.CharField(max_length=20, blank=True, default="")
    velocity_vx = models.FloatField(default=0.0)
    node_states = models.JSONField(default=list, blank=True)
    edge_states = models.JSONField(default=list, blank=True)
    action_state = models.JSONField(default=list, blank=True)
    loads = models.JSONField(default=list, blank=True)
    errors = models.JSONField(default=list, blank=True)
    information = models.JSONField(default=list, blank=True)
    amr_position = models.JSONField(default=dict, blank=True)
    velocity = models.JSONField(default=dict, blank=True)
    battery_state = models.JSONField(default=dict, blank=True)
    safety_state = models.JSONField(default=dict, blank=True)
    driving = models.BooleanField(default=False)
    paused = models.BooleanField(default=False)
    new_base_request = models.BooleanField(default=False)
    error_codes = models.JSONField(default=list, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "robot_telemetry"
        ordering = ["-recorded_at"]
        indexes = [
            models.Index(fields=["robot", "-recorded_at"], name="robot_tel_time_idx"),
            models.Index(fields=["order_id"], name="robot_tel_order_idx"),
        ]

    def __str__(self):
        return f"Telemetry {self.robot_id} at {self.recorded_at}"


# Tương thích tài liệu: AmrStateLog là tên nghiệp vụ của RobotTelemetry.
AmrStateLog = RobotTelemetry


def ensure_topic_controls(robot: RobotProfile) -> list[TopicControl]:
    """Tạo/đồng bộ 5 topic VDA 5050 cho robot (idempotent)."""
    from workers.acs_system import (
        connection_topic,
        factsheet_topic,
        instant_actions_topic,
        order_topic,
        state_topic,
    )

    definitions = [
        (state_topic(robot.manufacturer, robot.serial_number), "STATE"),
        (connection_topic(robot.manufacturer, robot.serial_number), "CONNECTION"),
        (order_topic(robot.manufacturer, robot.serial_number), "ORDER"),
        (instant_actions_topic(robot.manufacturer, robot.serial_number), "INSTANT_ACTIONS"),
        (factsheet_topic(robot.manufacturer, robot.serial_number), "FACTSHEET"),
    ]
    controls = []
    for topic, topic_type in definitions:
        control, _ = TopicControl.objects.update_or_create(
            topic=topic,
            defaults={
                "topic_type": topic_type,
                "amr": robot,
                "manufacturer": robot.manufacturer,
                "version": robot.version,
            },
        )
        controls.append(control)
    return controls

