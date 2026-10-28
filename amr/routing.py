from django.urls import re_path
from amr.consumers import RobotTelemetryConsumer

websocket_urlpatterns = [
    re_path(r'^ws/robot/telemetry/?$', RobotTelemetryConsumer.as_asgi()),
]
