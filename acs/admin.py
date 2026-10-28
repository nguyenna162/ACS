from django.contrib import admin

from acs.models import (
    ActionDispatch,
    MqttOutboundMessage,
    Order,
    OrderAction,
    OrderMaster,
    OrderNode,
    RealtimeEvent,
    SubOrder,
    SupplyRequest,
    SystemHeartbeat,
    SystemLog,
)


class OrderNodeInline(admin.TabularInline):
    model = OrderNode
    extra = 0


class SubOrderInline(admin.TabularInline):
    model = SubOrder
    extra = 0
    readonly_fields = ("sub_order_id",)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "order_id", "order_type", "order_status", "robot", "priority",
        "order_master", "created_at", "completed_at",
    )
    list_filter = ("order_status", "order_type", "priority")
    search_fields = ("order_id", "robot__serial_number")
    inlines = [OrderNodeInline]


@admin.register(OrderMaster)
class OrderMasterAdmin(admin.ModelAdmin):
    list_display = ("master_id", "status", "priority", "supply_request", "created_at", "completed_at")
    list_filter = ("status", "priority")
    search_fields = ("master_id", "supply_request__request_id")
    inlines = [SubOrderInline]


@admin.register(SubOrder)
class SubOrderAdmin(admin.ModelAdmin):
    list_display = (
        "sub_order_id", "order_master", "amr", "status", "order_update_id",
        "resend_count", "sent_at", "acked_at",
    )
    list_filter = ("status",)
    search_fields = ("amr__serial_number", "order_master__master_id")


@admin.register(SupplyRequest)
class SupplyRequestAdmin(admin.ModelAdmin):
    list_display = (
        "request_id", "source", "line_id", "product_model", "priority",
        "status", "amr", "sla_deadline", "created_at",
    )
    list_filter = ("status", "source", "priority")
    search_fields = ("request_id", "line_id", "product_model")


@admin.register(ActionDispatch)
class ActionDispatchAdmin(admin.ModelAdmin):
    list_display = ("action_id", "action_type", "blocking_type", "status", "amr", "created_at")
    list_filter = ("status", "action_type", "blocking_type")
    search_fields = ("action_id",)


@admin.register(SystemLog)
class SystemLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "category", "level", "message", "actor", "robot", "order")
    list_filter = ("category", "level")
    search_fields = ("message", "actor")
    readonly_fields = ("created_at",)

    def has_add_permission(self, request):
        return False


admin.site.register(OrderAction)


@admin.register(RealtimeEvent)
class RealtimeEventAdmin(admin.ModelAdmin):
    list_display = ("id", "topic", "created_at")
    list_filter = ("topic",)
    ordering = ("-id",)
    readonly_fields = ("topic", "payload", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(MqttOutboundMessage)
class MqttOutboundMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "topic", "state", "qos", "attempts", "next_attempt_at", "sent_at")
    list_filter = ("state", "qos")
    search_fields = ("topic", "last_error")
    ordering = ("-id",)
    readonly_fields = ("created_at", "sent_at")


@admin.register(SystemHeartbeat)
class SystemHeartbeatAdmin(admin.ModelAdmin):
    list_display = ("component", "instance_id", "last_seen", "started_at")
    readonly_fields = ("component", "instance_id", "started_at", "last_seen", "meta")

    def has_add_permission(self, request):
        return False
