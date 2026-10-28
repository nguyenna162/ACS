from django.contrib import admin

from buffer.models import (
    ActionOption,
    BufferSlot,
    BufferZone,
    Schedule,
    ScheduleStop,
    StopAction,
)


class BufferSlotInline(admin.TabularInline):
    model = BufferSlot
    extra = 0


@admin.register(BufferZone)
class BufferZoneAdmin(admin.ModelAdmin):
    list_display = ("zone_id", "zone_name", "zone_type", "capacity", "map_topology")
    list_filter = ("zone_type",)
    inlines = [BufferSlotInline]


@admin.register(BufferSlot)
class BufferSlotAdmin(admin.ModelAdmin):
    list_display = (
        "slot_id", "name", "buffer_type", "slot_status", "product_model",
        "enable", "handle_order", "updated_at",
    )
    list_filter = ("slot_status", "buffer_type", "enable", "zone")
    search_fields = ("slot_id", "name", "rssid", "pallet_code")


class ScheduleStopInline(admin.TabularInline):
    model = ScheduleStop
    extra = 0


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    list_display = ("name", "map", "number_of_amr_need", "number_of_active_order", "enable")
    list_filter = ("enable",)
    inlines = [ScheduleStopInline]


@admin.register(ScheduleStop)
class ScheduleStopAdmin(admin.ModelAdmin):
    list_display = ("schedule", "buffer", "role_id", "sequence")
    list_filter = ("schedule",)


@admin.register(StopAction)
class StopActionAdmin(admin.ModelAdmin):
    list_display = ("schedule_stop", "action", "action_option", "sequence")


@admin.register(ActionOption)
class ActionOptionAdmin(admin.ModelAdmin):
    list_display = ("name", "action_type", "description")
