from django.contrib import admin

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


class NodeInline(admin.TabularInline):
    model = Node
    extra = 0
@admin.register(MapBase)
class MapBaseAdmin(admin.ModelAdmin):
    list_display = ("name", "part", "version", "status", "created_at")
    list_filter = ("status", "part")
    search_fields = ("name", "part")


@admin.register(MapTopology)
class MapTopologyAdmin(admin.ModelAdmin):
    list_display = ("map_id", "map_name", "version", "is_active", "created_at")
    list_filter = ("is_active",)
    inlines = [NodeInline]


@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    list_display = ("node_id", "name", "node_type", "map_topology", "is_occupied", "use_node")
    list_filter = ("node_type", "is_occupied", "use_node", "map_topology")
    search_fields = ("node_id", "name")


@admin.register(Edge)
class EdgeAdmin(admin.ModelAdmin):
    list_display = ("edge_id", "start_node", "end_node", "distance", "direction", "is_locked")
    list_filter = ("direction", "is_locked")
    search_fields = ("edge_id",)


@admin.register(WorldMapLayer)
class WorldMapLayerAdmin(admin.ModelAdmin):
    list_display = ("name", "map", "resolution", "opacity")


@admin.register(GridMapLayer)
class GridMapLayerAdmin(admin.ModelAdmin):
    list_display = ("name", "map", "resize", "opacity")


@admin.register(ZoneSet)
class ZoneSetAdmin(admin.ModelAdmin):
    list_display = ("name", "map", "version", "is_active")
    list_filter = ("is_active",)


@admin.register(TrafficZone)
class TrafficZoneAdmin(admin.ModelAdmin):
    list_display = ("name", "zone_type", "zone_set", "max_occupancy", "priority", "max_speed")
    list_filter = ("zone_type", "zone_set")


@admin.register(Zone)
class ZoneAdmin(admin.ModelAdmin):
    list_display = ("zone_name", "zone_id", "map", "is_active")


@admin.register(Action)
class ActionAdmin(admin.ModelAdmin):
    list_display = ("action_type", "blocking_type", "description")
    list_filter = ("blocking_type",)


admin.site.register(ActionOnNode)
admin.site.register(ActionOnEdge)
