from rest_framework import serializers
from map.models import (
    Action,
    ActionOnEdge,
    ActionOnNode,
    Edge,
    GridMapLayer,
    MapBase,
    MapTopology,
    Node,
    TrafficZone,
    WorldMapLayer,
    Zone,
    ZoneSet,
)

class EdgeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Edge
        fields = [
            'edge_id', 'map', 'name', 'description', 'start_node', 'end_node',
            'distance', 'length', 'max_speed', 'orientation', 'orientation_type',
            'max_rotation_speed', 'rotation_allowed', 'trajectory', 'direction',
            'is_locked', 'locked_by', 'locked_at', 'traffic_zone', 'zone',
        ]

class NodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Node
        fields = [
            'node_id', 'map_topology', 'map', 'name', 'node_type', 'description',
            'pos_x', 'pos_y', 'theta', 'allow_deviation_xy', 'allow_deviation_theta',
            'use_node', 'is_init', 'is_extended', 'traffic_zone', 'zone',
            'is_occupied', 'locked_by', 'locked_at',
        ]

class MapTopologySerializer(serializers.ModelSerializer):
    nodes = NodeSerializer(many=True, read_only=True)

    class Meta:
        model = MapTopology
        fields = ['map_id', 'version', 'map_name', 'is_active', 'created_at', 'nodes']


class WorldMapLayerSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorldMapLayer
        fields = [
            'id', 'map', 'name', 'image', 'x_origin', 'y_origin', 'resolution',
            'resize', 'opacity', 'description',
        ]


class GridMapLayerSerializer(serializers.ModelSerializer):
    class Meta:
        model = GridMapLayer
        fields = [
            'id', 'map', 'name', 'image', 'x_set_background', 'y_set_background',
            'resize', 'opacity',
        ]


class MapBaseSerializer(serializers.ModelSerializer):
    world_layer = serializers.SerializerMethodField()
    grid_layer = serializers.SerializerMethodField()

    class Meta:
        model = MapBase
        fields = [
            'id', 'topology', 'name', 'part', 'version', 'status', 'description',
            'created_by', 'created_at', 'updated_at', 'world_layer', 'grid_layer',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_world_layer(self, obj):
        layer = WorldMapLayer.objects.filter(map=obj).first()
        return WorldMapLayerSerializer(layer).data if layer else None

    def get_grid_layer(self, obj):
        layer = GridMapLayer.objects.filter(map=obj).first()
        return GridMapLayerSerializer(layer).data if layer else None


class ZoneSetSerializer(serializers.ModelSerializer):
    class Meta:
        model = ZoneSet
        fields = ['id', 'map', 'name', 'description', 'version', 'is_active', 'created_at']
        read_only_fields = ['id', 'created_at']


class TrafficZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrafficZone
        fields = [
            'id', 'zone_set', 'map', 'name', 'zone_type', 'description',
            'max_occupancy', 'priority', 'max_speed', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class ZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = Zone
        fields = [
            'id', 'zone_id', 'zone_name', 'pos_x', 'pos_y', 'size_x', 'size_y',
            'is_active', 'aligo_metadata', 'map', 'zone_set', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class ActionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Action
        fields = ['id', 'action_type', 'blocking_type', 'parameters', 'description']
        read_only_fields = ['id']


class ActionOnNodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActionOnNode
        fields = ['id', 'action', 'node', 'sequence']
        read_only_fields = ['id']


class ActionOnEdgeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActionOnEdge
        fields = ['id', 'action', 'edge', 'sequence']
        read_only_fields = ['id']

