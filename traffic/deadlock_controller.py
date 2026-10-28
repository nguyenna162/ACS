"""Phát hiện và xử lý bế tắc giao thông (deadlock).

Mô hình chờ: robot A đang chờ node bị robot B giữ -> cạnh A -> B. Chu trình
trong đồ thị chờ chính là deadlock. Cách xử lý: nhường khóa của robot có mức ưu
tiên thấp nhất trong chu trình (robot sẽ tự xin lại ở chu kỳ sau), ghi audit.
"""

from __future__ import annotations

import logging

from workers.acs_system import TOPIC_ALERT, EventBus
from traffic.models import RobotPriority

logger = logging.getLogger("traffic.deadlock")


def build_wait_for_graph() -> dict[str, dict]:
    """Trả về {robot_serial: {'waits_for': serial|None, 'node': node_id, 'order': id}}."""
    from acs.models import OrderNode
    from map.models import Node
    from amr.models import RobotProfile

    graph: dict[str, dict] = {}
    executing = (
        RobotProfile.objects.filter(operational_state="RUNNING")
        .exclude(current_node__isnull=True)
        .values_list("serial_number", "current_node_id")
    )
    for serial_number, current_node in executing:
        order_node = (
            OrderNode.objects.filter(
                order__robot_id=serial_number,
                order__order_status="EXECUTING",
                node_status="PENDING",
            )
            .order_by("sequence_id")
            .values("node_id", "order_id", "sequence_id")
            .first()
        )
        if not order_node or order_node["node_id"] == current_node:
            continue
        node = Node.objects.filter(node_id=order_node["node_id"]).values(
            "is_occupied", "locked_by_id"
        ).first()
        waits_for = None
        if node and node["is_occupied"] and node["locked_by_id"] and node["locked_by_id"] != serial_number:
            waits_for = node["locked_by_id"]
        graph[serial_number] = {
            "waits_for": waits_for,
            "node": order_node["node_id"],
            "order": order_node["order_id"],
            "sequence_id": order_node["sequence_id"],
        }
    return graph


def find_cycles(graph: dict[str, dict]) -> list[list[str]]:
    cycles: list[list[str]] = []
    seen_cycles: set[frozenset] = set()
    visited: set[str] = set()
    for start in graph:
        if start in visited:
            continue
        path: list[str] = []
        cursor = start
        while cursor and cursor not in path:
            path.append(cursor)
            cursor = (graph.get(cursor) or {}).get("waits_for")
        if cursor in path:
            cycle = path[path.index(cursor):]
            signature = frozenset(cycle)
            if signature not in seen_cycles:
                seen_cycles.add(signature)
                cycles.append(cycle)
        visited.update(path)
    return cycles


def detect_deadlock() -> list[list[str]]:
    return find_cycles(build_wait_for_graph())


def _robot_priority(serial_number: str) -> int:
    from acs.models import Order

    priority = (
        Order.objects.filter(robot_id=serial_number, order_status="EXECUTING")
        .order_by("-priority")
        .values_list("priority", flat=True)
        .first()
    )
    return int(RobotPriority.coerce(priority if priority is not None else RobotPriority.NORMAL))


def resolve_deadlock(strategy: str = "lowest_priority_first") -> list[dict]:
    """Giải phóng khóa của robot ưu tiên thấp nhất trong mỗi chu trình."""
    from traffic.state_reservation_manager import ReservationManager

    manager = ReservationManager()
    actions: list[dict] = []
    for cycle in detect_deadlock():
        if not cycle:
            continue
        if strategy == "youngest_lock_first":
            victim = cycle[-1]
        else:
            victim = min(cycle, key=_robot_priority)
        released = manager.release_robot(victim)
        action = {"cycle": cycle, "victim": victim, "released": released}
        actions.append(action)
        logger.warning(
            "Deadlock %s - nhường khóa của %s (%s khóa)", cycle, victim, released
        )
        EventBus.publish(TOPIC_ALERT, {"type": "traffic_deadlock", **action})
    return actions
