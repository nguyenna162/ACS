from django.urls import include, path
from rest_framework.routers import DefaultRouter

from acs.api_views import (
    ActionDispatchViewSet,
    FleetScheduleView,
    InstantActionView,
    OrderMasterViewSet,
    OrderViewSet,
    SubOrderViewSet,
    SupplyRequestViewSet,
    SystemLogViewSet,
)
from acs.views import (
    HealthLiveView,
    HealthReadyView,
    MetricsView,
    WorkerHeartbeatView,
)
from amr.views import RobotViewSet, TopicControlViewSet
from buffer.views import (
    ActionOptionViewSet,
    BufferSlotViewSet,
    BufferZoneViewSet,
    ScheduleStopViewSet,
    ScheduleViewSet,
    StopActionViewSet,
)
from map.views import (
    ActionOnEdgeViewSet,
    ActionOnNodeViewSet,
    ActionViewSet,
    EdgeViewSet,
    GridMapLayerViewSet,
    MapBaseViewSet,
    MapTopologyViewSet,
    NodeViewSet,
    TrafficZoneViewSet,
    WorldMapLayerViewSet,
    ZoneSetViewSet,
    ZoneViewSet,
)

router = DefaultRouter()
router.register(r"robots", RobotViewSet, basename="robot")
router.register(r"robot-topics", TopicControlViewSet, basename="robot-topic")
router.register(r"topology", MapTopologyViewSet, basename="topology")
router.register(r"maps", MapBaseViewSet, basename="map-base")
router.register(r"map-world-layers", WorldMapLayerViewSet, basename="map-world-layer")
router.register(r"map-grid-layers", GridMapLayerViewSet, basename="map-grid-layer")
router.register(r"zone-sets", ZoneSetViewSet, basename="zone-set")
router.register(r"traffic-zones", TrafficZoneViewSet, basename="traffic-zone")
router.register(r"zones", ZoneViewSet, basename="zone")
router.register(r"nodes", NodeViewSet, basename="node")
router.register(r"edges", EdgeViewSet, basename="edge")
router.register(r"actions", ActionViewSet, basename="map-action")
router.register(r"action-on-nodes", ActionOnNodeViewSet, basename="action-on-node")
router.register(r"action-on-edges", ActionOnEdgeViewSet, basename="action-on-edge")
router.register(r"buffer-zones", BufferZoneViewSet, basename="buffer-zone")
router.register(r"buffer-slots", BufferSlotViewSet, basename="buffer-slot")
router.register(r"schedules", ScheduleViewSet, basename="schedule")
router.register(r"schedule-stops", ScheduleStopViewSet, basename="schedule-stop")
router.register(r"stop-actions", StopActionViewSet, basename="stop-action")
router.register(r"action-options", ActionOptionViewSet, basename="action-option")
router.register(r"orders", OrderViewSet, basename="order")
router.register(r"supply-requests", SupplyRequestViewSet, basename="supply-request")
router.register(r"order-masters", OrderMasterViewSet, basename="order-master")
router.register(r"sub-orders", SubOrderViewSet, basename="sub-order")
router.register(r"action-dispatches", ActionDispatchViewSet, basename="action-dispatch")
router.register(r"system-logs", SystemLogViewSet, basename="system-log")

urlpatterns = [
    path("health/live/", HealthLiveView.as_view(), name="health-live"),
    path("health/ready/", HealthReadyView.as_view(), name="health-ready"),
    path("health/metrics/", MetricsView.as_view(), name="health-metrics"),
    path("health/heartbeat/", WorkerHeartbeatView.as_view(), name="health-heartbeat"),
    path("", include(router.urls)),
    path("instant-actions/", InstantActionView.as_view(), name="instant-actions"),
    path("fleet/schedule/", FleetScheduleView.as_view(), name="fleet-schedule"),
]

