import logging
from django.db import transaction
from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from map.models import Node
from amr.models import RobotProfile

logger = logging.getLogger(__name__)

HORIZON_WINDOW_K = 3

class TrafficController:

    @staticmethod
    def _notify_traffic_change():
        try:
            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)('robot_telemetry', {
                    'type': 'traffic_update',
                    'data': {'timestamp': timezone.now().isoformat()}
                })
        except Exception:
            pass

    @staticmethod
    @transaction.atomic
    def update_sliding_window(order, current_node_id: str) -> bool:
        """
        1. Giải phóng các nút robot đã đi qua (trước current_node_id).
        2. Cấp phát khóa tối đa k=3 nút tiếp theo nếu không bị xe khác chiếm giữ.
        Trả về True nếu có sự thay đổi cờ released để phát bản tin cập nhật order.
        """
        from acs.models import OrderNode

        robot = RobotProfile.objects.select_for_update().get(pk=order.robot_id)
        order_nodes = list(OrderNode.objects.select_for_update().filter(order=order).order_by('sequence_id'))

        if not order_nodes:
            return False

        # Xác định vị trí hiện tại trong chuỗi lộ trình
        current_idx = 0
        for idx, onode in enumerate(order_nodes):
            if onode.node_id == current_node_id:
                current_idx = idx
                break

        # Bước 1: Mở khóa các nút đã đi qua
        passed_node_ids = [on.node_id for on in order_nodes[:current_idx]]
        if passed_node_ids:
            Node.objects.select_for_update().filter(
                node_id__in=passed_node_ids, locked_by=robot
            ).update(is_occupied=False, locked_by=None, locked_at=None)
            OrderNode.objects.filter(
                id__in=[on.id for on in order_nodes[:current_idx]]
            ).update(node_status='PASSED')

        # Bước 2: Xét cửa sổ k=3 nút tiếp theo từ current_idx
        window_candidates = order_nodes[current_idx : current_idx + HORIZON_WINDOW_K]
        candidate_ids = [on.node_id for on in window_candidates]

        # Khóa dòng theo thứ tự bảng chữ cái của node_id để tránh DB-level deadlock
        db_nodes = {
            n.node_id: n
            for n in Node.objects.select_for_update().filter(node_id__in=sorted(candidate_ids)).order_by('node_id')
        }

        released_changed = False
        blocked = False

        for onode in window_candidates:
            target_node = db_nodes.get(onode.node_id)
            if not target_node:
                continue

            if not blocked and (not target_node.is_occupied or target_node.locked_by_id == robot.serial_number):
                # Cấp khóa nút
                target_node.is_occupied = True
                target_node.locked_by = robot
                target_node.locked_at = timezone.now()
                target_node.save(update_fields=['is_occupied', 'locked_by', 'locked_at'])

                if not onode.released:
                    onode.released = True
                    onode.save(update_fields=['released'])
                    released_changed = True
            else:
                # Phát hiện nút giao N_x đã bị robot khác khóa -> Dừng cấp phát từ N_x trở đi
                blocked = True
                if onode.released:
                    onode.released = False
                    onode.save(update_fields=['released'])
                    released_changed = True

        # Cập nhật trạng thái vận hành của Robot
        new_state = 'BLOCKED' if blocked else 'RUNNING'
        if robot.operational_state != new_state:
            robot.operational_state = new_state
            robot.save(update_fields=['operational_state'])

        if released_changed:
            order.order_update_id += 1
            order.save(update_fields=['order_update_id'])

        TrafficController._notify_traffic_change()
        return released_changed

    @staticmethod
    @transaction.atomic
    def release_order_locks(order) -> None:
        """
        Giải phóng toàn bộ các nút do order của robot đang giữ khi hoàn thành hoặc hủy đơn.
        """
        if order.robot_id:
            Node.objects.filter(locked_by_id=order.robot_id).update(
                is_occupied=False, locked_by=None, locked_at=None
            )
            RobotProfile.objects.filter(pk=order.robot_id).update(operational_state='IDLE')
            TrafficController._notify_traffic_change()
