import uuid

from django.db import models

class MapTopology(models.Model):
    map_id = models.CharField(max_length=64, primary_key=True)
    version = models.CharField(max_length=32, default='1.0.0')
    map_name = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'map_topology'

    def __str__(self):
        return f"{self.map_name} (v{self.version})"


class Node(models.Model):
    NODE_TYPES = [
        ("WAYPOINT", "Waypoint"),
        ("INTERSECTION", "Intersection"),
        ("BUFFER", "Buffer Point"),
        ("CHARGER", "Charging Point"),
        ("PARKING", "Parking"),
        ("LINE", "Production Line"),
    ]

    node_id = models.CharField(max_length=64, primary_key=True)
    map_topology = models.ForeignKey(MapTopology, on_delete=models.CASCADE, related_name='nodes')
    map = models.ForeignKey(
        'map.MapBase', on_delete=models.SET_NULL, null=True, blank=True, related_name='nodes'
    )
    name = models.CharField(max_length=128, blank=True, default='')
    node_type = models.CharField(max_length=16, choices=NODE_TYPES, default='WAYPOINT')
    description = models.CharField(max_length=255, blank=True, default='')
    pos_x = models.FloatField()
    pos_y = models.FloatField()
    theta = models.FloatField(default=0.0)
    allow_deviation_xy = models.FloatField(default=0.0)
    allow_deviation_theta = models.FloatField(default=0.0)
    use_node = models.BooleanField(default=True)
    is_init = models.BooleanField(default=False)
    is_extended = models.BooleanField(default=False)
    traffic_zone = models.ForeignKey(
        'map.TrafficZone', on_delete=models.SET_NULL, null=True, blank=True, related_name='nodes'
    )
    zone = models.ForeignKey(
        'map.Zone', on_delete=models.SET_NULL, null=True, blank=True, related_name='nodes'
    )
    is_occupied = models.BooleanField(default=False)
    locked_by = models.ForeignKey(
        'amr.RobotProfile', on_delete=models.SET_NULL, null=True, blank=True, related_name='locked_nodes'
    )
    locked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'map_node'
        indexes = [
            models.Index(fields=['map_topology', 'is_occupied'], name='map_node_topology_occ_idx'),
            models.Index(fields=['use_node', 'node_type'], name='map_node_use_type_idx'),
        ]

    def __str__(self):
        occupied_str = f" [Occupied by {self.locked_by_id}]" if self.is_occupied else ""
        return f"Node {self.node_id} ({self.pos_x}, {self.pos_y}){occupied_str}"


class Edge(models.Model):
    DIRECTION_CHOICES = [('UNI', 'Unidirectional'), ('BI', 'Bidirectional')]
    ORIENTATION_TYPES = [('TANGENTIAL', 'Tangential'), ('FIXED', 'Fixed')]

    edge_id = models.CharField(max_length=64, primary_key=True)
    map = models.ForeignKey(
        'map.MapBase', on_delete=models.SET_NULL, null=True, blank=True, related_name='edges'
    )
    name = models.CharField(max_length=128, blank=True, default='')
    description = models.CharField(max_length=255, blank=True, default='')
    start_node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name='outgoing_edges')
    end_node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name='incoming_edges')
    distance = models.FloatField()
    length = models.FloatField(default=0.0)
    max_speed = models.FloatField(default=1.2)
    orientation = models.FloatField(default=0.0)
    orientation_type = models.CharField(
        max_length=16, choices=ORIENTATION_TYPES, default='TANGENTIAL'
    )
    max_rotation_speed = models.FloatField(default=0.0)
    rotation_allowed = models.BooleanField(default=True)
    trajectory = models.JSONField(default=list, blank=True)
    traffic_zone = models.ForeignKey(
        'map.TrafficZone', on_delete=models.SET_NULL, null=True, blank=True, related_name='edges'
    )
    zone = models.ForeignKey(
        'map.Zone', on_delete=models.SET_NULL, null=True, blank=True, related_name='edges'
    )
    is_locked = models.BooleanField(default=False)
    locked_by = models.ForeignKey(
        'amr.RobotProfile', on_delete=models.SET_NULL, null=True, blank=True, related_name='locked_edges'
    )
    locked_at = models.DateTimeField(null=True, blank=True)
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES, default='UNI')

    class Meta:
        db_table = 'map_edge'
        indexes = [
            models.Index(fields=['start_node', 'end_node'], name='map_edge_start_end_idx'),
            models.Index(fields=['is_locked'], name='map_edge_locked_idx'),
        ]

    def __str__(self):
        arrow = "<->" if self.direction == 'BI' else "->"
        return f"Edge {self.edge_id}: {self.start_node_id} {arrow} {self.end_node_id} ({self.distance}m)"


class MapBase(models.Model):
    """Bản đồ gốc theo tài liệu (``map`` module).

    ``topology`` nối sang ``MapTopology`` cũ để tương thích toàn bộ node/edge
    hiện có (một bản đồ có thể có nhiều phiên bản layer/zone).
    """

    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('ACTIVE', 'Active'),
        ('ARCHIVED', 'Archived'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    topology = models.OneToOneField(
        MapTopology, on_delete=models.CASCADE, null=True, blank=True, related_name='base'
    )
    name = models.CharField(max_length=128)
    part = models.CharField(max_length=64, blank=True, default='')
    version = models.CharField(max_length=32, default='1.0.0')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='DRAFT')
    description = models.CharField(max_length=255, blank=True, default='')
    created_by = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='acs_maps'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'map_base'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'name'], name='map_base_status_name_idx')]

    def __str__(self):
        return f"{self.name} v{self.version} [{self.status}]"


class WorldMapLayer(models.Model):
    """Layer ảnh nền thế giới (ảnh bản đồ do người dùng upload)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    map = models.OneToOneField(MapBase, on_delete=models.CASCADE, related_name='world_layer')
    name = models.CharField(max_length=128, default='world')
    # Tài liệu dùng ImageField; môi trường offline không cài Pillow nên dùng
    # FileField (HMI tự kiểm tra định dạng ảnh khi upload).
    image = models.FileField(upload_to='maps/world/%Y/%m/', blank=True, default='')
    x_origin = models.FloatField(default=0.0)
    y_origin = models.FloatField(default=0.0)
    resolution = models.FloatField(default=1.0)
    resize = models.FloatField(default=1.0)
    opacity = models.FloatField(default=1.0)
    description = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        db_table = 'map_world_layer'

    def __str__(self):
        return f"WorldLayer {self.name} ({self.map_id})"


class GridMapLayer(models.Model):
    """Layer lưới toạ độ (grid) chồng lên bản đồ nền."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    map = models.OneToOneField(MapBase, on_delete=models.CASCADE, related_name='grid_layer')
    name = models.CharField(max_length=128, default='grid')
    image = models.FileField(upload_to='maps/grid/%Y/%m/', blank=True, default='')
    x_set_background = models.IntegerField(default=0)
    y_set_background = models.IntegerField(default=0)
    resize = models.FloatField(default=1.0)
    opacity = models.FloatField(default=1.0)

    class Meta:
        db_table = 'map_grid_layer'

    def __str__(self):
        return f"GridLayer {self.name} ({self.map_id})"


class ZoneSet(models.Model):
    """Tập hợp zone VDA 5050 gắn với một phiên bản bản đồ."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    map = models.ForeignKey(MapBase, on_delete=models.CASCADE, related_name='zone_sets')
    name = models.CharField(max_length=128)
    description = models.CharField(max_length=255, blank=True, default='')
    version = models.CharField(max_length=32, default='1.0.0')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'map_zone_set'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['map', 'is_active'], name='zoneset_map_active_idx')]

    def __str__(self):
        return f"ZoneSet {self.name} v{self.version}"


class TrafficZone(models.Model):
    ZONE_TYPES = [
        ('LINE', 'Production Line'),
        ('CROSSING', 'Crossing'),
        ('BUFFER', 'Buffer'),
        ('CHARGING', 'Charging'),
        ('PARKING', 'Parking'),
        ('BLOCKED', 'Blocked'),
        ('SPEED_LIMIT', 'Speed Limit'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    zone_set = models.ForeignKey(ZoneSet, on_delete=models.CASCADE, related_name='zones')
    map = models.ForeignKey(MapBase, on_delete=models.CASCADE, related_name='traffic_zones')
    name = models.CharField(max_length=128)
    zone_type = models.CharField(max_length=16, choices=ZONE_TYPES, default='LINE')
    description = models.CharField(max_length=255, blank=True, default='')
    max_occupancy = models.PositiveIntegerField(default=1)
    priority = models.IntegerField(default=0)
    max_speed = models.FloatField(default=1.2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'map_traffic_zone'
        ordering = ['name']
        indexes = [models.Index(fields=['zone_set', 'zone_type'], name='tzone_set_type_idx')]

    def __str__(self):
        return f"TrafficZone {self.name} [{self.zone_type}]"


class Zone(models.Model):
    """Vùng giao thông legacy (hình chữ nhật) - tương thích dữ liệu Aligo."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    zone_id = models.CharField(max_length=64, blank=True, default='')
    zone_name = models.CharField(max_length=128)
    pos_x = models.FloatField(default=0.0)
    pos_y = models.FloatField(default=0.0)
    size_x = models.FloatField(default=0.0)
    size_y = models.FloatField(default=0.0)
    is_active = models.BooleanField(default=True)
    aligo_metadata = models.JSONField(default=dict, blank=True)
    map = models.ForeignKey(
        MapBase, on_delete=models.SET_NULL, null=True, blank=True, related_name='legacy_zones'
    )
    zone_set = models.ForeignKey(
        ZoneSet, on_delete=models.SET_NULL, null=True, blank=True, related_name='legacy_zones'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'map_zone'
        indexes = [models.Index(fields=['zone_id'], name='map_zone_external_idx')]

    def __str__(self):
        return f"Zone {self.zone_name} ({self.pos_x},{self.pos_y})"


class Action(models.Model):
    """Hành động VDA 5050 có thể gắn vào node hoặc edge."""

    BLOCKING_TYPES = [('NONE', 'None'), ('SOFT', 'Soft'), ('HARD', 'Hard')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    action_type = models.CharField(max_length=64)
    blocking_type = models.CharField(max_length=10, choices=BLOCKING_TYPES, default='HARD')
    parameters = models.JSONField(default=list, blank=True)
    description = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        db_table = 'map_action'
        ordering = ['action_type']

    def __str__(self):
        return f"Action {self.action_type} ({self.blocking_type})"


class ActionOnNode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    action = models.ForeignKey(Action, on_delete=models.CASCADE, related_name='nodes')
    node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name='node_actions')
    sequence = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'map_action_on_node'
        ordering = ['sequence']
        constraints = [
            models.UniqueConstraint(fields=['action', 'node'], name='uniq_action_node')
        ]

    def __str__(self):
        return f"{self.action.action_type}@{self.node_id}"


class ActionOnEdge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    action = models.ForeignKey(Action, on_delete=models.CASCADE, related_name='edges')
    edge = models.ForeignKey(Edge, on_delete=models.CASCADE, related_name='edge_actions')
    sequence = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'map_action_on_edge'
        ordering = ['sequence']
        constraints = [
            models.UniqueConstraint(fields=['action', 'edge'], name='uniq_action_edge')
        ]

    def __str__(self):
        return f"{self.action.action_type}@{self.edge_id}"



