import uuid

from django.db import models

class BufferZone(models.Model):
    ZONE_TYPES = [
        ('STORAGE', 'Material Storage'),
        ('LINE', 'Assembly Line'),
        ('PARKING', 'Parking Area'),
        ('CHARGING', 'Charging Station'),
    ]
    zone_id = models.CharField(max_length=64, primary_key=True)
    map_topology = models.ForeignKey('map.MapTopology', on_delete=models.CASCADE)
    zone_name = models.CharField(max_length=128)
    zone_type = models.CharField(max_length=32, choices=ZONE_TYPES)
    capacity = models.IntegerField(default=1)

    class Meta:
        db_table = 'buffer_zone'

    def __str__(self):
        return f"{self.zone_name} ({self.zone_type})"


class BufferSlot(models.Model):
    SLOT_STATUS = [
        ('EMPTY', 'Empty'),
        ('RESERVED', 'Reserved by Order'),
        ('OCCUPIED', 'Occupied'),
    ]
    BUFFER_TYPES = [
        ('STORAGE', 'Storage'),
        ('LINE', 'Production Line'),
        ('PICK', 'Pick Station'),
        ('DROP', 'Drop Station'),
        ('CHARGING', 'Charging'),
    ]

    slot_id = models.CharField(max_length=64, primary_key=True)
    zone = models.ForeignKey(BufferZone, on_delete=models.CASCADE, related_name='slots')
    node = models.OneToOneField('map.Node', on_delete=models.CASCADE, related_name='buffer_slot')
    name = models.CharField(max_length=128, blank=True, default='')
    rssid = models.CharField(max_length=64, unique=True, null=True, blank=True)
    buffer_type = models.CharField(max_length=16, choices=BUFFER_TYPES, default='STORAGE')
    product_model = models.CharField(max_length=64, blank=True, default='')
    process = models.CharField(max_length=64, blank=True, default='')
    role_id = models.CharField(max_length=64, blank=True, default='')
    slot_status = models.CharField(max_length=16, choices=SLOT_STATUS, default='EMPTY')
    enable = models.BooleanField(default=True)
    ip_address = models.GenericIPAddressField(protocol='IPv4', null=True, blank=True)
    pallet_code = models.CharField(max_length=64, null=True, blank=True)
    handle_order = models.ForeignKey(
        'acs.Order', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='handling_buffers',
    )
    last_success_transfer = models.DateTimeField(null=True, blank=True)
    time_gap_btw_transfer = models.DurationField(null=True, blank=True)
    data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'buffer_slot'
        indexes = [
            models.Index(fields=['slot_status', 'enable'], name='buffer_status_enable_idx'),
            models.Index(fields=['zone', 'slot_status'], name='buffer_zone_status_idx'),
        ]

    def __str__(self):
        return f"Slot {self.slot_id} [{self.slot_status}] (Pallet: {self.pallet_code or 'None'})"


class ActionOption(models.Model):
    """Lựa chọn hành động tại điểm dừng (theo tài liệu: ``ActionOption``)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=64, unique=True)
    action_type = models.CharField(max_length=64, blank=True, default='')
    description = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        db_table = 'buffer_action_option'
        ordering = ['name']

    def __str__(self):
        return self.name


class Schedule(models.Model):
    """Lịch trình cấp vật tư định kỳ cho một dây chuyền/khu vực."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128)
    description = models.CharField(max_length=255, blank=True, default='')
    map = models.ForeignKey(
        'map.MapBase', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='schedules',
    )
    number_of_amr_need = models.PositiveIntegerField(default=1)
    number_of_active_order = models.PositiveIntegerField(default=0)
    enable = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'buffer_schedule'
        ordering = ['name']
        indexes = [models.Index(fields=['enable'], name='schedule_enable_idx')]

    def __str__(self):
        return f"Schedule {self.name} (AMR cần: {self.number_of_amr_need})"


class ScheduleStop(models.Model):
    """Điểm dừng trong lịch trình (buffer đích + vai trò + thứ tự)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schedule = models.ForeignKey(Schedule, on_delete=models.CASCADE, related_name='stops')
    buffer = models.ForeignKey(
        BufferSlot, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='schedule_stops',
    )
    role_id = models.CharField(max_length=64, blank=True, default='')
    sequence = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'buffer_schedule_stop'
        ordering = ['sequence']
        indexes = [models.Index(fields=['schedule', 'sequence'], name='sched_stop_seq_idx')]

    def __str__(self):
        return f"{self.schedule.name} -> {self.buffer_id} (#{self.sequence})"


class StopAction(models.Model):
    """Hành động thực hiện khi dừng tại điểm dừng."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schedule_stop = models.ForeignKey(
        ScheduleStop, on_delete=models.CASCADE, related_name='stop_actions'
    )
    action = models.ForeignKey(
        'map.Action', on_delete=models.CASCADE, null=True, blank=True,
        related_name='stop_actions',
    )
    action_option = models.ForeignKey(
        ActionOption, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='stop_actions',
    )
    sequence = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'buffer_stop_action'
        ordering = ['sequence']

    def __str__(self):
        return f"StopAction {self.schedule_stop_id} #{self.sequence}"


