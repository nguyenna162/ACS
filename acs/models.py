import uuid

from django.db import models
from django.utils import timezone


class Order(models.Model):
    ORDER_STATUS = [
        ('CREATED', 'Created'),
        ('ASSIGNED', 'Assigned to Robot'),
        ('EXECUTING', 'Executing'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
        ('FAILED', 'Failed'),
    ]
    ORDER_TYPES = [
        ('TRANSPORT', 'Transport'),
        ('TRANSFER', 'Buffer Transfer'),
        ('CHARGE', 'Charging'),
        ('PARK', 'Parking'),
        ('RETURN', 'Return to Home'),
    ]
    order_id = models.CharField(max_length=64, primary_key=True)
    order_type = models.CharField(max_length=16, choices=ORDER_TYPES, default='TRANSPORT')
    order_master = models.ForeignKey(
        'acs.OrderMaster', on_delete=models.SET_NULL, null=True, blank=True, related_name='orders'
    )
    robot = models.ForeignKey('amr.RobotProfile', on_delete=models.SET_NULL, null=True, blank=True)
    src_slot = models.ForeignKey('buffer.BufferSlot', on_delete=models.PROTECT, related_name='source_orders')
    dst_slot = models.ForeignKey('buffer.BufferSlot', on_delete=models.PROTECT, related_name='dest_orders')
    order_update_id = models.IntegerField(default=0)
    order_status = models.CharField(max_length=20, choices=ORDER_STATUS, default='CREATED')
    priority = models.IntegerField(default=1)
    queued_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'acs_order'
        indexes = [
            models.Index(fields=['order_status', 'created_at'], name='acs_order_status_created_idx'),
            models.Index(fields=['robot', 'order_status'], name='acs_order_robot_status_idx'),
        ]

    def __str__(self):
        return f"Order {self.order_id} ({self.order_status}, Robot: {self.robot_id})"

    @property
    def is_terminal(self) -> bool:
        return self.order_status in ('COMPLETED', 'CANCELLED', 'FAILED')



class OrderNode(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='order_nodes')
    node = models.ForeignKey('map.Node', on_delete=models.PROTECT)
    sequence_id = models.IntegerField()  # Số chẵn: 0, 2, 4...
    released = models.BooleanField(default=False)
    node_status = models.CharField(max_length=20, default='PENDING')  # PENDING, PASSED

    class Meta:
        db_table = 'acs_order_node'
        ordering = ['sequence_id']

    def __str__(self):
        return f"{self.order_id} - Node {self.node_id} (seq={self.sequence_id}, released={self.released})"


class OrderAction(models.Model):
    ACTION_TYPES = [('pick', 'Pick Pallet'), ('drop', 'Drop Pallet'), ('charge', 'Charge Battery')]
    BLOCKING_TYPES = [('NONE', 'None'), ('SOFT', 'Soft'), ('HARD', 'Hard')]

    action_id = models.CharField(max_length=64, primary_key=True)
    order_node = models.ForeignKey(OrderNode, on_delete=models.CASCADE, related_name='actions')
    action_type = models.CharField(max_length=20, choices=ACTION_TYPES)
    blocking_type = models.CharField(max_length=10, choices=BLOCKING_TYPES, default='HARD')
    action_status = models.CharField(max_length=20, default='WAITING')
    params = models.JSONField(default=list)

    class Meta:
        db_table = 'acs_order_action'

    def __str__(self):
        return f"Action {self.action_id} ({self.action_type}, blocking={self.blocking_type})"


class SupplyRequest(models.Model):
    """Yêu cầu cấp vật tư (từ Aligo MES hoặc nhập tay) - đầu vào bộ ghép cặp.

    Theo tài liệu: worker ``acs_supply_request_pairing`` ghép yêu cầu với AMR
    theo tầng ưu tiên, tie-break bằng khoảng cách đồ thị (Dijkstra) và hạn SLA.
    """

    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('PAIRED', 'Paired'),
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
        ('CANCELLED', 'Cancelled'),
    ]
    SOURCE_CHOICES = [
        ('ALIGO', 'Aligo MES'),
        ('MANUAL', 'Manual'),
        ('ERP', 'ERP'),
    ]

    id = models.BigAutoField(primary_key=True)
    request_id = models.CharField(max_length=64, unique=True)
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default='MANUAL')
    line_id = models.CharField(max_length=64, blank=True, default='')
    process = models.CharField(max_length=64, blank=True, default='')
    product_model = models.CharField(max_length=64, blank=True, default='')
    quantity = models.PositiveIntegerField(default=1)
    priority = models.IntegerField(default=1)
    sla_deadline = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='PENDING')
    src_slot = models.ForeignKey(
        'buffer.BufferSlot', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='supply_requests_from',
    )
    dst_slot = models.ForeignKey(
        'buffer.BufferSlot', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='supply_requests_to',
    )
    amr = models.ForeignKey(
        'amr.RobotProfile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='supply_requests',
    )
    order_master = models.ForeignKey(
        'acs.OrderMaster', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='supply_requests',
    )
    payload = models.JSONField(default=dict, blank=True)
    outcome = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'acs_supply_request'
        ordering = ['-priority', 'sla_deadline', 'created_at']
        indexes = [
            models.Index(fields=['status', 'priority', 'created_at'], name='supply_status_prio_idx'),
            models.Index(fields=['line_id', 'status'], name='supply_line_status_idx'),
        ]

    def __str__(self):
        return f"SupplyRequest {self.request_id} [{self.status}]"


class OrderMaster(models.Model):
    """Đơn hàng tổng (parent) điều phối nhiều SubOrder cho nhiều AMR."""

    STATUS_CHOICES = [
        ('NEW', 'New'),
        ('PLANNING', 'Planning'),
        ('PROCESSING', 'Processing'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
        ('CANCELLED', 'Cancelled'),
    ]

    id = models.BigAutoField(primary_key=True)
    master_id = models.CharField(max_length=64, unique=True)
    supply_request = models.ForeignKey(
        SupplyRequest, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='order_masters',
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='NEW')
    priority = models.IntegerField(default=1)
    plan = models.JSONField(default=dict, blank=True)
    outcome = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'acs_order_master'
        ordering = ['created_at', 'id']
        indexes = [
            models.Index(fields=['status', 'created_at'], name='master_status_created_idx'),
            models.Index(fields=['status', 'priority'], name='master_status_prio_idx'),
        ]

    def __str__(self):
        return f"OrderMaster {self.master_id} [{self.status}]"

    @property
    def is_terminal(self) -> bool:
        return self.status in ('COMPLETED', 'FAILED', 'CANCELLED')

    def refresh_status(self) -> str:
        """Tính lại trạng thái master từ các sub-order con."""
        statuses = list(self.sub_orders.values_list('status', flat=True))
        if not statuses:
            return self.status
        if all(status == 'COMPLETED' for status in statuses):
            new_status = 'COMPLETED'
        elif any(status == 'FAILED' for status in statuses):
            new_status = 'FAILED'
        elif any(status in ('SENT', 'ACKED', 'EXECUTING', 'COMPLETED') for status in statuses):
            new_status = 'PROCESSING'
        elif self.status == 'CANCELLED':
            new_status = 'CANCELLED'
        elif self.status == 'PROCESSING':
            # Không hạ cấp master khi SubOrder vẫn PENDING nhưng Order đã được tạo.
            new_status = 'PROCESSING'
        else:
            new_status = 'PLANNING'
        if new_status != self.status:
            self.status = new_status
            if new_status == 'COMPLETED':
                self.completed_at = timezone.now()
                self.save(update_fields=['status', 'completed_at'])
            else:
                self.save(update_fields=['status'])
        return new_status


class MasterIdSequence(models.Model):
    """Bộ sinh mã master tuần tự theo ngày (không phụ thuộc đồng hồ máy trạm)."""

    day = models.DateField(primary_key=True)
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'acs_master_id_sequence'

    @classmethod
    def next_value(cls, day=None) -> int:
        from django.db import transaction

        day = day or timezone.localdate()
        with transaction.atomic():
            row, _ = cls.objects.select_for_update().get_or_create(day=day)
            row.last_value += 1
            row.save(update_fields=['last_value'])
            return row.last_value


class SubOrder(models.Model):
    """Đơn hàng con: một chặng vận chuyển giao cho một AMR (theo tài liệu)."""

    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('SENT', 'Sent'),
        ('ACKED', 'Acknowledged'),
        ('EXECUTING', 'Executing'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
        ('CANCELLED', 'Cancelled'),
    ]

    id = models.BigAutoField(primary_key=True)
    sub_order_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    order_master = models.ForeignKey(
        OrderMaster, on_delete=models.CASCADE, related_name='sub_orders'
    )
    order = models.OneToOneField(
        'acs.Order', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sub_order',
    )
    amr = models.ForeignKey(
        'amr.RobotProfile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sub_orders',
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='PENDING')
    priority = models.IntegerField(default=1)
    path = models.JSONField(default=dict, blank=True)
    anchor_seq = models.BigIntegerField(null=True, blank=True)
    order_update_id = models.BigIntegerField(default=0)
    resend_count = models.PositiveIntegerField(default=0)
    outcome = models.CharField(max_length=255, blank=True, default='')
    sent_at = models.DateTimeField(null=True, blank=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    acked_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'acs_sub_order'
        ordering = ['created_at', 'id']
        indexes = [
            models.Index(fields=['status', 'created_at'], name='sub_status_created_idx'),
            models.Index(fields=['amr', 'status'], name='sub_amr_status_idx'),
            models.Index(fields=['order_master', 'status'], name='sub_master_status_idx'),
        ]

    def __str__(self):
        return f"SubOrder {self.sub_order_id} [{self.status}] amr={self.amr_id}"

    @property
    def is_terminal(self) -> bool:
        return self.status in ('COMPLETED', 'FAILED', 'CANCELLED')

    def mark_sent(self, anchor_seq: int | None = None) -> None:
        now = timezone.now()
        self.status = 'SENT'
        self.anchor_seq = anchor_seq
        self.sent_at = self.sent_at or now
        self.last_sent_at = now
        self.save(update_fields=['status', 'anchor_seq', 'sent_at', 'last_sent_at', 'updated_at'])

    def mark_acked(self) -> None:
        self.status = 'ACKED'
        self.acked_at = timezone.now()
        self.save(update_fields=['status', 'acked_at', 'updated_at'])

    def mark_failed(self, reason: str) -> None:
        self.status = 'FAILED'
        self.outcome = (reason or '')[:255]
        self.completed_at = timezone.now()
        self.save(update_fields=['status', 'outcome', 'completed_at', 'updated_at'])


class ActionDispatch(models.Model):
    """Theo dõi từng action node/edge đã phát xuống AMR (khớp ảnh tài liệu)."""

    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('SENT', 'Sent'),
        ('ACKNOWLEDGED', 'Acknowledged'),
        ('EXECUTING', 'Executing'),
        ('FINISHED', 'Finished'),
        ('FAILED', 'Failed'),
    ]

    action_id = models.CharField(max_length=64, primary_key=True)
    sub_order = models.ForeignKey(
        SubOrder, on_delete=models.CASCADE, null=True, blank=True,
        related_name='action_dispatches',
    )
    order = models.ForeignKey(
        'acs.Order', on_delete=models.CASCADE, null=True, blank=True,
        related_name='action_dispatches',
    )
    amr = models.ForeignKey(
        'amr.RobotProfile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='action_dispatches',
    )
    action_type = models.CharField(max_length=32, blank=True, default='')
    blocking_type = models.CharField(max_length=10, blank=True, default='NONE')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='PENDING')
    parameters = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'acs_action_dispatch'
        ordering = ['created_at', 'action_id']
        indexes = [
            models.Index(fields=['status', 'created_at'], name='action_status_created_idx'),
            models.Index(fields=['amr', 'status'], name='action_amr_status_idx'),
        ]

    def __str__(self):
        return f"ActionDispatch {self.action_id} [{self.status}]"


class SystemLog(models.Model):
    """Nhật ký hệ thống theo phân hệ (order, sync, AMR state, buffer...)."""

    CATEGORY_CHOICES = [
        ('ORDER', 'Order'),
        ('ROBOT', 'AMR'),
        ('TRAFFIC', 'Traffic'),
        ('MQTT', 'MQTT'),
        ('BUFFER', 'Buffer'),
        ('ALIGO', 'Aligo'),
        ('API', 'API'),
        ('SYSTEM', 'System'),
    ]
    LEVEL_CHOICES = [
        ('DEBUG', 'Debug'),
        ('INFO', 'Info'),
        ('WARNING', 'Warning'),
        ('ERROR', 'Error'),
        ('CRITICAL', 'Critical'),
    ]

    id = models.BigAutoField(primary_key=True)
    category = models.CharField(max_length=16, choices=CATEGORY_CHOICES, default='SYSTEM')
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default='INFO')
    message = models.TextField()
    actor = models.CharField(max_length=64, blank=True, default='system')
    robot = models.ForeignKey(
        'amr.RobotProfile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='system_logs',
    )
    order = models.ForeignKey(
        'acs.Order', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='system_logs',
    )
    sub_order = models.ForeignKey(
        SubOrder, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='system_logs',
    )
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'acs_system_log'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['category', '-created_at'], name='syslog_category_time_idx'),
            models.Index(fields=['level', '-created_at'], name='syslog_level_time_idx'),
        ]

    def __str__(self):
        return f"[{self.category}/{self.level}] {self.message[:80]}"

    @classmethod
    def write(cls, message: str, category: str = 'SYSTEM', level: str = 'INFO',
              actor: str = 'system', **fields) -> 'SystemLog':
        return cls.objects.create(
            message=message, category=category, level=level, actor=actor, **fields
        )


# Tương thích tài liệu: bảng Log trong module acs.
Log = SystemLog


# ---------------------------------------------------------------------------
# Hạ tầng offline (được gộp từ module `core`): Event Outbox, MQTT Outbox,
# nhịp sống tiến trình.
# ---------------------------------------------------------------------------
class RealtimeEvent(models.Model):
    """Sự kiện nghiệp vụ (order/traffic/alert) đẩy tới HMI.

    Chỉ lưu sự kiện "thưa" (vòng đời đơn, giao thông, cảnh báo). Trạng thái
    robot được truyền qua snapshot 1s/lần nên không tạo event cho mỗi bản tin
    telemetry (50 AMR x 1Hz sẽ làm phình bảng).
    """

    id = models.BigAutoField(primary_key=True)
    topic = models.CharField(max_length=48, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "core_realtime_event"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["topic", "id"], name="core_event_topic_id_idx"),
            models.Index(fields=["created_at"], name="core_event_created_idx"),
        ]

    def __str__(self):
        return f"[{self.topic}] #{self.id}"


class MqttOutboundMessage(models.Model):
    """Hàng đợi gửi MQTT bền vững (Transactional Outbox)."""

    STATE_PENDING = "PENDING"
    STATE_SENT = "SENT"
    STATE_FAILED = "FAILED"
    STATE_CHOICES = [
        (STATE_PENDING, "Pending"),
        (STATE_SENT, "Sent"),
        (STATE_FAILED, "Failed"),
    ]

    id = models.BigAutoField(primary_key=True)
    topic = models.CharField(max_length=255, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    qos = models.PositiveSmallIntegerField(default=1)
    retain = models.BooleanField(default=False)
    state = models.CharField(max_length=10, choices=STATE_CHOICES, default=STATE_PENDING, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        db_table = "core_mqtt_outbox"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["state", "next_attempt_at"], name="core_outbox_state_next_idx"),
            models.Index(fields=["topic", "created_at"], name="core_outbox_topic_created_idx"),
        ]

    def __str__(self):
        return f"{self.topic} [{self.state}] attempts={self.attempts}"


class SystemHeartbeat(models.Model):
    """Nhịp sống của tiến trình. Web/worker cập nhật định kỳ để phát hiện treo."""

    component = models.CharField(max_length=48, primary_key=True)
    instance_id = models.CharField(max_length=128, blank=True, default="")
    started_at = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now, db_index=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_system_heartbeat"
        ordering = ["component"]

    def __str__(self):
        return f"{self.component} @ {self.last_seen:%Y-%m-%d %H:%M:%S}"

    @classmethod
    def record(cls, component: str, instance_id: str = "", meta: dict | None = None):
        obj, created = cls.objects.get_or_create(
            component=component,
            defaults={
                "instance_id": instance_id,
                "started_at": timezone.now(),
                "last_seen": timezone.now(),
                "meta": meta or {},
            },
        )
        if not created:
            obj.instance_id = instance_id or obj.instance_id
            obj.last_seen = timezone.now()
            if meta is not None:
                obj.meta = meta
            obj.save(update_fields=["instance_id", "last_seen", "meta"])
        return obj

    def age_seconds(self, now=None) -> float:
        now = now or timezone.now()
        return max(0.0, (now - self.last_seen).total_seconds())



