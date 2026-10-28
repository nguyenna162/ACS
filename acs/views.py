from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie


@ensure_csrf_cookie
def dashboard_view(request):
    return render(request, "dashboard.html", {"active": "dashboard", "page_title": "Điều hành đội xe"})


@ensure_csrf_cookie
def map_editor_view(request):
    return render(request, "map_editor.html", {"active": "map", "page_title": "Bản đồ & Topology"})


@ensure_csrf_cookie
def orders_view(request):
    return render(request, "orders.html", {"active": "orders", "page_title": "Quản lý đơn hàng"})


@ensure_csrf_cookie
def amr_view(request):
    return render(request, "amr.html", {"active": "amr", "page_title": "Quản lý đội xe AMR"})


@ensure_csrf_cookie
def system_view(request):
    return render(request, "system.html", {"active": "system", "page_title": "Quản trị hệ thống"})


# ---------------------------------------------------------------------------
# Endpoint sức khỏe hệ thống (monitoring nội bộ nhà máy).
# ---------------------------------------------------------------------------
from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workers.acs_system import metrics, readiness
from acs.models import SystemHeartbeat


def _permission_class():
    return AllowAny if settings.ACS_HEALTH_PUBLIC else IsAuthenticated


class HealthLiveView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(
            {
                "status": "ok",
                "instance": settings.ACS_INSTANCE_NAME,
                "time": timezone.now().isoformat(),
            }
        )


class HealthReadyView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        healthy, payload = readiness()
        return Response(
            payload,
            status=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        )


class MetricsView(APIView):
    permission_classes = [_permission_class()]

    def get(self, request):
        return Response(metrics())


class WorkerHeartbeatView(APIView):
    """Cho phép tiến trình ngoài (script/systemd watchdog) báo nhịp sống."""

    permission_classes = [_permission_class()]

    def post(self, request):
        component = str(request.data.get("component", "")).strip()
        if not component:
            return Response({"detail": "component là bắt buộc"}, status=status.HTTP_400_BAD_REQUEST)
        SystemHeartbeat.record(
            component=component,
            instance_id=str(request.data.get("instance_id", ""))[:128],
            meta=request.data.get("meta") or {},
        )
        return Response({"status": "ok", "component": component})

