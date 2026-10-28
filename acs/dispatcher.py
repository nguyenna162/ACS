from django.conf import settings
from django.db import transaction
from django.utils import timezone
from amr.models import RobotProfile
from buffer.models import BufferSlot
from acs.models import Order, OrderNode, OrderAction
from map.pathfinding import a_star_search
from map.traffic_lock import TrafficController

W1_DISTANCE = getattr(settings, 'ACS_DISPATCH_W1_DISTANCE', 0.7)
W2_BATTERY = getattr(settings, 'ACS_DISPATCH_W2_BATTERY', 0.3)
MIN_BATTERY_THRESHOLD = getattr(settings, 'ACS_MIN_BATTERY_PERCENT', 25.0)
D_MAX_MAP = getattr(settings, 'ACS_D_MAX_MAP', 100.0)  # Chiều dài chuẩn hóa tối đa (m)


class OrderDispatcher:

    @staticmethod
    @transaction.atomic
    def create_and_dispatch_order(order_id: str, src_slot_id: str, dst_slot_id: str, priority: int = 1,
                                  robot_serial: str | None = None,
                                  order_type: str = 'TRANSPORT',
                                  order_master=None) -> Order:
        # 1. Kiểm tra tính hợp lệ và giữ chỗ tại App buffer
        src_slot = BufferSlot.objects.select_for_update().get(pk=src_slot_id)
        dst_slot = BufferSlot.objects.select_for_update().get(pk=dst_slot_id)

        if src_slot.slot_status != 'OCCUPIED':
            raise ValueError(f"Ô nguồn {src_slot_id} không có hàng (Trạng thái: {src_slot.slot_status}).")
        if dst_slot.slot_status != 'EMPTY':
            raise ValueError(f"Ô đích {dst_slot_id} không trống (Trạng thái: {dst_slot.slot_status}).")

        dst_slot.slot_status = 'RESERVED'
        dst_slot.save(update_fields=['slot_status', 'updated_at'])

        # 2. Lọc ứng viên: robot IDLE, pin >= ngưỡng; hoặc robot do MES chỉ định
        candidate_qs = RobotProfile.objects.select_for_update().filter(
            battery_level__gte=MIN_BATTERY_THRESHOLD
        )
        if robot_serial:
            candidate_qs = candidate_qs.filter(serial_number=robot_serial).exclude(
                operational_state='OFFLINE'
            )
        else:
            candidate_qs = candidate_qs.filter(operational_state='IDLE')
        candidates = list(candidate_qs)

        best_robot = None
        min_cost = float('inf')
        best_path_to_src = []

        for robot in candidates:
            if not robot.current_node_id:
                continue
            if Order.objects.filter(robot=robot, order_status='EXECUTING').exists():
                continue

            if robot.current_node_id == src_slot.node_id:
                path_to_src = [robot.current_node_id]
                dist = 0.0
            else:
                path_to_src, dist = a_star_search(robot.current_node_id, src_slot.node_id)

            if path_to_src is None:
                continue

            cost = W1_DISTANCE * (dist / D_MAX_MAP) + W2_BATTERY * (1.0 - (robot.battery_level / 100.0))
            if cost < min_cost:
                min_cost = cost
                best_robot = robot
                best_path_to_src = path_to_src

        if not best_robot:
            # Revert reservation if no robot available
            dst_slot.slot_status = 'EMPTY'
            dst_slot.save(update_fields=['slot_status', 'updated_at'])
            raise RuntimeError(
                "Không tìm thấy robot khả dụng thỏa mãn điều kiện pin (>= "
                f"{MIN_BATTERY_THRESHOLD:.0f}%) và lộ trình."
            )


        # 3. Tìm lộ trình từ điểm lấy hàng (src_slot) tới điểm trả hàng (dst_slot)
        path_src_to_dst, _ = a_star_search(src_slot.node_id, dst_slot.node_id)
        if not path_src_to_dst:
            dst_slot.slot_status = 'EMPTY'
            dst_slot.save(update_fields=['slot_status', 'updated_at'])
            raise RuntimeError("Không tìm thấy đường đi hợp lệ từ điểm lấy hàng tới điểm trả hàng.")

        full_node_path = best_path_to_src[:-1] + path_src_to_dst

        # 4. Khởi tạo bản ghi Order và chuỗi OrderNode + OrderAction
        order = Order.objects.create(
            order_id=order_id,
            order_type=order_type,
            order_master=order_master,
            robot=best_robot,
            src_slot=src_slot,
            dst_slot=dst_slot,
            order_status='EXECUTING',
            priority=priority,
            queued_at=timezone.now(),
            started_at=timezone.now(),
        )
        best_robot.is_idle = False
        best_robot.save(update_fields=['is_idle'])
        BufferSlot.objects.filter(pk__in=[src_slot.pk, dst_slot.pk]).update(handle_order=order)


        pick_assigned = False
        for idx, n_id in enumerate(full_node_path):
            onode = OrderNode.objects.create(
                order=order,
                node_id=n_id,
                sequence_id=idx * 2,  # Số chẵn theo chuẩn VDA 5050
                released=False
            )
            if n_id == src_slot.node_id and not pick_assigned:
                OrderAction.objects.create(
                    action_id=f"ACT_PICK_{order_id}",
                    order_node=onode,
                    action_type='pick',
                    blocking_type='HARD',
                    params=[{"key": "stationType", "value": src_slot.slot_id}]
                )
                pick_assigned = True
            elif idx == len(full_node_path) - 1 and n_id == dst_slot.node_id:
                OrderAction.objects.create(
                    action_id=f"ACT_DROP_{order_id}",
                    order_node=onode,
                    action_type='drop',
                    blocking_type='HARD',
                    params=[{"key": "stationType", "value": dst_slot.slot_id}]
                )

        # 5. Kích hoạt khóa cửa sổ trượt k=3 nút đầu tiên
        TrafficController.update_sliding_window(order, best_robot.current_node_id)
        return order

    @staticmethod
    @transaction.atomic
    def cancel_order(order_id: str) -> Order:
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.order_status in ('COMPLETED', 'CANCELLED'):
            return order

        order.order_status = 'CANCELLED'
        order.completed_at = timezone.now()
        order.save(update_fields=['order_status', 'completed_at'])

        # Giải phóng ô đích nếu đang reserved
        if order.dst_slot.slot_status == 'RESERVED':
            order.dst_slot.slot_status = 'EMPTY'
            order.dst_slot.save(update_fields=['slot_status', 'updated_at'])

        TrafficController.release_order_locks(order)
        return order
