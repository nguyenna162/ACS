from django.db import transaction
from buffer.models import BufferSlot, BufferZone
from amr.models import RobotProfile

class BufferAllocator:

    @staticmethod
    @transaction.atomic
    def reserve_slot(slot_id: str) -> BufferSlot:
        slot = BufferSlot.objects.select_for_update().get(pk=slot_id)
        if slot.slot_status != 'EMPTY':
            raise ValueError(f"Slot {slot_id} is not empty (Current status: {slot.slot_status}).")
        slot.slot_status = 'RESERVED'
        slot.save(update_fields=['slot_status', 'updated_at'])
        return slot

    @staticmethod
    @transaction.atomic
    def occupy_slot(slot_id: str, pallet_code: str = None) -> BufferSlot:
        slot = BufferSlot.objects.select_for_update().get(pk=slot_id)
        slot.slot_status = 'OCCUPIED'
        if pallet_code:
            slot.pallet_code = pallet_code
        slot.save(update_fields=['slot_status', 'pallet_code', 'updated_at'])
        return slot

    @staticmethod
    @transaction.atomic
    def release_slot(slot_id: str) -> BufferSlot:
        slot = BufferSlot.objects.select_for_update().get(pk=slot_id)
        slot.slot_status = 'EMPTY'
        slot.pallet_code = None
        slot.save(update_fields=['slot_status', 'pallet_code', 'updated_at'])
        return slot

    @staticmethod
    def get_zone_summary():
        zones = BufferZone.objects.prefetch_related('slots').all()
        summary = []
        for z in zones:
            total_slots = z.slots.count()
            empty_slots = z.slots.filter(slot_status='EMPTY').count()
            occupied_slots = z.slots.filter(slot_status='OCCUPIED').count()
            reserved_slots = z.slots.filter(slot_status='RESERVED').count()
            summary.append({
                'zone_id': z.zone_id,
                'zone_name': z.zone_name,
                'zone_type': z.zone_type,
                'capacity': z.capacity,
                'total_slots': total_slots,
                'empty_slots': empty_slots,
                'occupied_slots': occupied_slots,
                'reserved_slots': reserved_slots,
            })
        return summary
