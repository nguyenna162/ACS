from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from buffer.allocator import BufferAllocator
from buffer.models import ActionOption, BufferSlot, BufferZone, Schedule, ScheduleStop, StopAction
from buffer.serializers import (
    ActionOptionSerializer,
    BufferSlotSerializer,
    BufferZoneSerializer,
    ScheduleSerializer,
    ScheduleStopSerializer,
    StopActionSerializer,
)


class BufferZoneViewSet(viewsets.ModelViewSet):
    queryset = BufferZone.objects.all()
    serializer_class = BufferZoneSerializer
    filterset_fields = ['map_topology', 'zone_type']

    @action(detail=False, methods=['get'])
    def summary(self, request):
        return Response(BufferAllocator.get_zone_summary())


class BufferSlotViewSet(viewsets.ModelViewSet):
    queryset = BufferSlot.objects.all().select_related('zone', 'node')
    serializer_class = BufferSlotSerializer
    filterset_fields = ['zone', 'slot_status', 'buffer_type', 'enable']
    search_fields = ['slot_id', 'name', 'rssid', 'pallet_code', 'product_model']

    @action(detail=True, methods=['post'])
    def occupy(self, request, pk=None):
        pallet_code = request.data.get('pallet_code', 'PALLET_MANUAL')
        slot = BufferAllocator.occupy_slot(pk, pallet_code)
        return Response(BufferSlotSerializer(slot).data)

    @action(detail=True, methods=['post'])
    def release(self, request, pk=None):
        slot = BufferAllocator.release_slot(pk)
        return Response(BufferSlotSerializer(slot).data)


class ScheduleViewSet(viewsets.ModelViewSet):
    queryset = Schedule.objects.prefetch_related('stops__stop_actions').all()
    serializer_class = ScheduleSerializer
    filterset_fields = ['enable', 'map']


class ScheduleStopViewSet(viewsets.ModelViewSet):
    queryset = ScheduleStop.objects.select_related('schedule', 'buffer').all()
    serializer_class = ScheduleStopSerializer
    filterset_fields = ['schedule', 'buffer', 'role_id']


class StopActionViewSet(viewsets.ModelViewSet):
    queryset = StopAction.objects.select_related('action', 'action_option').all()
    serializer_class = StopActionSerializer
    filterset_fields = ['schedule_stop', 'action']


class ActionOptionViewSet(viewsets.ModelViewSet):
    queryset = ActionOption.objects.all()
    serializer_class = ActionOptionSerializer

