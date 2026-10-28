from django.contrib import admin

from amr.models import RobotProfile, RobotTelemetry, TopicControl


@admin.register(RobotProfile)
class RobotProfileAdmin(admin.ModelAdmin):
    list_display = (
        "serial_number", "manufacturer", "operational_state", "battery_level",
        "is_online", "is_idle", "order_id", "last_heartbeat",
    )
    list_filter = ("operational_state", "is_online", "is_idle", "manufacturer")
    search_fields = ("serial_number", "order_id", "amr_model")


@admin.register(TopicControl)
class TopicControlAdmin(admin.ModelAdmin):
    list_display = ("topic", "topic_type", "amr", "header_id", "last_call")
    list_filter = ("topic_type",)
    search_fields = ("topic", "amr__serial_number")


@admin.register(RobotTelemetry)
class RobotTelemetryAdmin(admin.ModelAdmin):
    list_display = ("id", "robot", "order_id", "operating_mode", "battery_state", "recorded_at")
    list_filter = ("operating_mode",)
    search_fields = ("robot__serial_number", "order_id")
    readonly_fields = ("recorded_at",)

    def has_add_permission(self, request):
        return False
