from django.conf import settings
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from amr.models import RobotProfile, TopicControl, ensure_topic_controls
from amr.serializers import RobotProfileSerializer, TopicControlSerializer
from amr.services import RobotService, telemetry_snapshot


class RobotViewSet(viewsets.ModelViewSet):
    queryset = RobotProfile.objects.all().order_by("serial_number")
    serializer_class = RobotProfileSerializer
    filterset_fields = ["operational_state", "is_online", "is_idle", "is_used_by_master"]
    search_fields = ["serial_number", "manufacturer", "order_id"]

    @action(detail=True, methods=["post"])
    def trigger_heartbeat(self, request, pk=None):
        robot = self.get_object()
        RobotProfile.objects.filter(pk=robot.pk).update(last_heartbeat=timezone.now())
        robot.refresh_from_db(fields=["last_heartbeat"])
        RobotService.broadcast_telemetry(robot)
        return Response(
            {
                "status": "heartbeat recorded",
                "serial_number": robot.serial_number,
                "last_heartbeat": robot.last_heartbeat,
            }
        )

    @action(detail=False, methods=["get"])
    def fleet_summary(self, request):
        robots = list(RobotProfile.objects.all())
        by_state: dict[str, int] = {}
        for robot in robots:
            by_state[robot.operational_state] = by_state.get(robot.operational_state, 0) + 1
        return Response(
            {
                "total": len(robots),
                "online": sum(1 for robot in robots if robot.is_online),
                "idle": sum(1 for robot in robots if robot.is_idle),
                "by_state": by_state,
                "low_battery": [
                    robot.serial_number
                    for robot in robots
                    if robot.battery_level < settings.ACS_MIN_BATTERY_PERCENT
                ],
                "robots": [telemetry_snapshot(robot) for robot in robots],
            }
        )

    @action(detail=True, methods=["get"])
    def snapshot(self, request, pk=None):
        return Response(telemetry_snapshot(self.get_object()))

    @action(detail=True, methods=["get"])
    def topics(self, request, pk=None):
        robot = self.get_object()
        controls = TopicControl.objects.filter(amr=robot)
        if not controls.exists():
            ensure_topic_controls(robot)
            controls = TopicControl.objects.filter(amr=robot)
        return Response(TopicControlSerializer(controls, many=True).data)

    @action(detail=True, methods=["get"])
    def telemetry(self, request, pk=None):
        robot = self.get_object()
        try:
            limit = min(500, max(1, int(request.query_params.get("limit", 50))))
        except (TypeError, ValueError):
            limit = 50
        rows = robot.telemetries.order_by("-recorded_at")[:limit]
        from amr.serializers import RobotTelemetrySerializer

        return Response(RobotTelemetrySerializer(rows, many=True).data)


class TopicControlViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TopicControl.objects.select_related("amr").all()
    serializer_class = TopicControlSerializer
    filterset_fields = ["topic_type", "amr"]

