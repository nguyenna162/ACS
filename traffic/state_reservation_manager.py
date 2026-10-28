"""Đặt chỗ node/edge theo thời gian (state + resource reservation).

Nguyên tắc: một robot chỉ được đi vào node/edge chưa bị robot khác chiếm. Khóa
có TTL để tự giải phóng khi worker/robot mất kết nối (tránh deadlock cứng).
Toàn bộ thao tác dùng UPDATE có điều kiện nên an toàn khi nhiều worker chạy.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from traffic.models import ReservationStatus

logger = logging.getLogger("traffic.reservation")


def _robot_ref(robot):
    if robot is None:
        return None
    if isinstance(robot, str):
        from amr.models import RobotProfile

        return RobotProfile.objects.filter(pk=robot).first()
    return robot


class ReservationManager:
    def __init__(self, ttl_seconds: float | None = None):
        self._ttl_seconds = ttl_seconds

    @property
    def ttl_seconds(self) -> float:
        if self._ttl_seconds is not None:
            return max(1.0, float(self._ttl_seconds))
        return max(1.0, float(getattr(settings, "ACS_RESERVATION_TTL_SECONDS", 45.0)))

    # -- giải phóng -------------------------------------------------------
    def release_expired(self) -> int:
        from map.models import Edge, Node

        cutoff = timezone.now() - timezone.timedelta(seconds=self.ttl_seconds)
        released = Node.objects.filter(is_occupied=True, locked_at__lt=cutoff).update(
            is_occupied=False, locked_by=None, locked_at=None
        )
        released += Edge.objects.filter(is_locked=True, locked_at__lt=cutoff).update(
            is_locked=False, locked_by=None, locked_at=None
        )
        if released:
            logger.info("Giải phóng %s khóa giao thông quá hạn", released)
        return released

    def release_robot(self, robot, node_ids: list[str] | None = None) -> int:
        from map.models import Edge, Node

        robot = _robot_ref(robot)
        if robot is None:
            return 0
        nodes = Node.objects.filter(locked_by=robot)
        if node_ids:
            nodes = nodes.filter(node_id__in=node_ids)
        released = nodes.update(is_occupied=False, locked_by=None, locked_at=None)
        released += Edge.objects.filter(locked_by=robot).update(
            is_locked=False, locked_by=None, locked_at=None
        )
        return released

    # -- đặt chỗ ----------------------------------------------------------
    def reserve_node(self, robot, node_id: str, ttl_seconds: float | None = None) -> bool:
        from map.models import Node

        robot = _robot_ref(robot)
        if robot is None:
            return False
        ttl = max(1.0, float(ttl_seconds or self.ttl_seconds))
        cutoff = timezone.now() - timezone.timedelta(seconds=ttl)
        updated = Node.objects.filter(node_id=node_id).filter(
            Q(is_occupied=False) | Q(locked_by=robot) | Q(locked_at__lt=cutoff)
        ).update(is_occupied=True, locked_by=robot, locked_at=timezone.now())
        if not updated:
            logger.debug("Node %s đang bị chiếm, không cấp cho %s", node_id, robot.pk)
        return bool(updated)

    def reserve_edge(self, robot, edge_id: str, ttl_seconds: float | None = None) -> bool:
        from map.models import Edge

        robot = _robot_ref(robot)
        if robot is None:
            return False
        ttl = max(1.0, float(ttl_seconds or self.ttl_seconds))
        cutoff = timezone.now() - timezone.timedelta(seconds=ttl)
        updated = Edge.objects.filter(edge_id=edge_id).filter(
            Q(is_locked=False) | Q(locked_by=robot) | Q(locked_at__lt=cutoff)
        ).update(is_locked=True, locked_by=robot, locked_at=timezone.now())
        return bool(updated)

    def release_node(self, node_id: str) -> int:
        from map.models import Node

        return Node.objects.filter(node_id=node_id, is_occupied=True).update(
            is_occupied=False, locked_by=None, locked_at=None
        )

    def reserve_nodes(self, robot, node_ids: list[str], ttl_seconds: float | None = None):
        """Đặt chỗ tuần tự; trả về danh sách node lấy được (rỗng nếu thất bại)."""
        acquired: list[str] = []
        for node_id in node_ids:
            if self.reserve_node(robot, node_id, ttl_seconds):
                acquired.append(node_id)
            else:
                break
        if len(acquired) != len(node_ids):
            if acquired:
                self.release_robot(robot, acquired)
            return []
        return acquired

    # -- truy vấn ---------------------------------------------------------
    def node_status(self, node_id: str, ttl_seconds: float | None = None) -> ReservationStatus:
        from map.models import Node

        node = Node.objects.filter(node_id=node_id).first()
        if node is None:
            return ReservationStatus.FREE
        if not node.is_occupied:
            return ReservationStatus.FREE
        ttl = max(1.0, float(ttl_seconds or self.ttl_seconds))
        if node.locked_at and (timezone.now() - node.locked_at).total_seconds() > ttl:
            return ReservationStatus.FREE
        if node.locked_by_id:
            return ReservationStatus.RESERVED
        return ReservationStatus.OCCUPIED

    def first_blocking_node(self, node_ids: list[str], robot) -> str | None:
        from map.models import Node

        robot = _robot_ref(robot)
        return (
            Node.objects.filter(node_id__in=node_ids, is_occupied=True)
            .exclude(locked_by=robot)
            .order_by("node_id")
            .values_list("node_id", flat=True)
            .first()
        )

    def reserve_path(self, robot, node_ids: list[str], ttl_seconds: float | None = None):
        """Đặt chỗ toàn bộ đường đi (dùng cho path đã cam kết với robot)."""
        with transaction.atomic():
            return self.reserve_nodes(robot, node_ids, ttl_seconds)

