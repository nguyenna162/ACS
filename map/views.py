import json
import uuid as _uuid

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

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
from map.serializers import (
    ActionOnEdgeSerializer,
    ActionOnNodeSerializer,
    ActionSerializer,
    EdgeSerializer,
    GridMapLayerSerializer,
    MapBaseSerializer,
    MapTopologySerializer,
    NodeSerializer,
    TrafficZoneSerializer,
    WorldMapLayerSerializer,
    ZoneSerializer,
    ZoneSetSerializer,
)
from traffic.deadlock_controller import detect_deadlock
from traffic.graph_distance import invalidate_cache, shortest_path


class MapTopologyViewSet(viewsets.ModelViewSet):
    queryset = MapTopology.objects.all()
    serializer_class = MapTopologySerializer

    @action(detail=False, methods=['get'])
    def full_map(self, request):
        active_map = MapTopology.objects.filter(is_active=True).first()
        if not active_map:
            return Response({'error': 'No active map found'}, status=404)

        nodes = Node.objects.filter(map_topology=active_map)
        edges = Edge.objects.filter(start_node__map_topology=active_map)
        base = MapBase.objects.filter(topology=active_map).first()

        return Response({
            'topology': MapTopologySerializer(active_map).data,
            'map_base': MapBaseSerializer(base).data if base else None,
            'nodes': NodeSerializer(nodes, many=True).data,
            'edges': EdgeSerializer(edges, many=True).data,
            'deadlocks': detect_deadlock(),
        })

    @action(detail=False, methods=['get'])
    def deadlocks(self, request):
        cycles = detect_deadlock()
        return Response({'deadlock_cycles': cycles, 'has_deadlock': len(cycles) > 0})

    @action(detail=False, methods=['get'])
    def path(self, request):
        start = request.query_params.get('start')
        goal = request.query_params.get('goal')
        if not start or not goal:
            return Response({'error': 'start và goal là bắt buộc'}, status=400)
        distance, path = shortest_path(start, goal)
        return Response({'start': start, 'goal': goal, 'distance': distance, 'path': path})

    @action(detail=False, methods=['post'])
    def import_map(self, request):
        """Import topology (node/edge/zone) từ JSON body hoặc file .json.

        Chấp nhận kết quả `/api/topology/full_map/` hoặc object gọn:
        {map_id?, map_name, version?, nodes:[...], edges:[...], zones?:[...]}
        """
        data = None
        uploaded_name = ""
        if request.content_type and "json" in request.content_type:
            data = request.data
        elif request.FILES.get("file"):
            uploaded_name = request.FILES["file"].name
            raw = request.FILES["file"].read()
            try:
                data = json.loads(raw.decode("utf-8-sig"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return Response({"error": "File không phải JSON hợp lệ."}, status=400)
        elif request.data.get("data"):
            try:
                data = json.loads(str(request.data.get("data")))
            except json.JSONDecodeError:
                return Response({"error": "Trường `data` không phải JSON hợp lệ."}, status=400)

        if not isinstance(data, dict):
            return Response({"error": "Dữ liệu JSON phải là một object."}, status=400)

        topo = data.get("topology") or {}
        map_id = str(data.get("map_id") or topo.get("map_id") or "").strip()
        map_name = str(data.get("map_name") or topo.get("map_name") or map_id or "IMPORTED_MAP")
        version = str(data.get("version") or topo.get("version") or "1.0.0")
        is_active = bool(data.get("is_active", topo.get("is_active", True)))
        if not map_id:
            map_id = f"MAP_{_uuid.uuid4().hex[:8].upper()}"

        topology, _ = MapTopology.objects.update_or_create(
            map_id=map_id,
            defaults={"map_name": map_name, "version": version, "is_active": is_active},
        )

        created_nodes = updated_nodes = 0
        for n in data.get("nodes") or []:
            if not isinstance(n, dict) or not n.get("node_id"):
                continue
            _, created = Node.objects.update_or_create(
                node_id=str(n["node_id"]),
                defaults={
                    "map_topology": topology,
                    "name": str(n.get("name", "")),
                    "node_type": str(n.get("node_type", "WAYPOINT"))[:16],
                    "description": str(n.get("description", ""))[:255],
                    "pos_x": float(n.get("pos_x", 0.0) or 0.0),
                    "pos_y": float(n.get("pos_y", 0.0) or 0.0),
                    "theta": float(n.get("theta", 0.0) or 0.0),
                    "use_node": bool(n.get("use_node", True)),
                    "is_init": bool(n.get("is_init", False)),
                    "is_extended": bool(n.get("is_extended", False)),
                },
            )
            if created:
                created_nodes += 1
            else:
                updated_nodes += 1

        created_edges = updated_edges = 0
        for e in data.get("edges") or []:
            if not isinstance(e, dict) or not e.get("edge_id"):
                continue
            start = str(e.get("start_node") or "")
            end = str(e.get("end_node") or "")
            if not start or not end:
                continue
            if not (Node.objects.filter(node_id=start).exists() and Node.objects.filter(node_id=end).exists()):
                continue
            _, created = Edge.objects.update_or_create(
                edge_id=str(e["edge_id"]),
                defaults={
                    "start_node_id": start,
                    "end_node_id": end,
                    "distance": float(e.get("distance", 0.0) or 0.0),
                    "direction": str(e.get("direction", "UNI"))[:10],
                    "max_speed": float(e.get("max_speed", 1.2) or 0.0),
                    "name": str(e.get("name", "")),
                    "description": str(e.get("description", "")),
                },
            )
            if created:
                created_edges += 1
            else:
                updated_edges += 1

        created_zones = updated_zones = 0
        for z in data.get("zones") or []:
            if not isinstance(z, dict) or not z.get("zone_name"):
                continue
            defaults = {
                "zone_id": str(z.get("zone_id", "")),
                "pos_x": float(z.get("pos_x", 0.0) or 0.0),
                "pos_y": float(z.get("pos_y", 0.0) or 0.0),
                "size_x": float(z.get("size_x", 0.0) or 0.0),
                "size_y": float(z.get("size_y", 0.0) or 0.0),
                "is_active": bool(z.get("is_active", True)),
            }
            zone_id = str(z.get("zone_id") or "").strip()
            obj = Zone.objects.filter(zone_id=zone_id).first() if zone_id else None
            if obj:
                for key, value in defaults.items():
                    setattr(obj, key, value)
                obj.save()
                updated_zones += 1
            else:
                Zone.objects.create(zone_name=str(z["zone_name"]), **defaults)
                created_zones += 1

        invalidate_cache()
        return Response({
            "status": "ok",
            "map_id": map_id,
            "map_name": map_name,
            "file": uploaded_name,
            "nodes": {"created": created_nodes, "updated": updated_nodes},
            "edges": {"created": created_edges, "updated": updated_edges},
            "zones": {"created": created_zones, "updated": updated_zones},
        })


class NodeViewSet(viewsets.ModelViewSet):
    queryset = Node.objects.all()
    serializer_class = NodeSerializer
    filterset_fields = ['map_topology', 'node_type', 'is_occupied', 'use_node']

    def perform_create(self, serializer):
        serializer.save()
        invalidate_cache()

    def perform_update(self, serializer):
        serializer.save()
        invalidate_cache()


class EdgeViewSet(viewsets.ModelViewSet):
    queryset = Edge.objects.all()
    serializer_class = EdgeSerializer
    filterset_fields = ['start_node', 'end_node', 'direction']

    def perform_create(self, serializer):
        serializer.save()
        invalidate_cache()

    def perform_update(self, serializer):
        serializer.save()
        invalidate_cache()


class MapBaseViewSet(viewsets.ModelViewSet):
    queryset = MapBase.objects.select_related('world_layer', 'grid_layer').all()
    serializer_class = MapBaseSerializer
    filterset_fields = ['status', 'part']


class WorldMapLayerViewSet(viewsets.ModelViewSet):
    queryset = WorldMapLayer.objects.all()
    serializer_class = WorldMapLayerSerializer


class GridMapLayerViewSet(viewsets.ModelViewSet):
    queryset = GridMapLayer.objects.all()
    serializer_class = GridMapLayerSerializer


class ZoneSetViewSet(viewsets.ModelViewSet):
    queryset = ZoneSet.objects.prefetch_related('zones').all()
    serializer_class = ZoneSetSerializer
    filterset_fields = ['map', 'is_active']


class TrafficZoneViewSet(viewsets.ModelViewSet):
    queryset = TrafficZone.objects.select_related('zone_set', 'map').all()
    serializer_class = TrafficZoneSerializer
    filterset_fields = ['zone_set', 'map', 'zone_type']


class ZoneViewSet(viewsets.ModelViewSet):
    queryset = Zone.objects.all()
    serializer_class = ZoneSerializer
    filterset_fields = ['map', 'zone_set', 'is_active']


class ActionViewSet(viewsets.ModelViewSet):
    queryset = Action.objects.all()
    serializer_class = ActionSerializer
    filterset_fields = ['action_type', 'blocking_type']


class ActionOnNodeViewSet(viewsets.ModelViewSet):
    queryset = ActionOnNode.objects.select_related('action', 'node').all()
    serializer_class = ActionOnNodeSerializer
    filterset_fields = ['node', 'action']


class ActionOnEdgeViewSet(viewsets.ModelViewSet):
    queryset = ActionOnEdge.objects.select_related('action', 'edge').all()
    serializer_class = ActionOnEdgeSerializer
    filterset_fields = ['edge', 'action']

