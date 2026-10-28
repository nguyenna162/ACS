"""Bộ lập lịch đội xe: Hungarian tối thiểu hóa tổng deadhead distance.

Theo tài liệu: ``traffic/fleet_scheduler.py`` gán nhiệm vụ cho AMR bằng
``scipy.optimize.linear_sum_assignment``; khi thiếu SciPy thì dùng greedy.
Module này dùng solver nội bộ (tương đương SciPy) và tự fallback greedy.

Chi phí: ``W1 * (distance / D_MAX_MAP) + W2 * (1 - battery/100)``.
Nhiệm vụ được xử lý theo tầng ưu tiên (RobotPriority) rồi tie-break bằng SLA.
"""

from __future__ import annotations

import logging

from django.conf import settings

from traffic.graph_distance import INF, distances_from, get_graph
from traffic.hungarian import BIG_COST, greedy_assignment, solve_assignment
from traffic.models import (
    FleetAssignment,
    FleetScheduleResult,
    FleetTask,
    RobotPriority,
    RobotSnapshot,
)

logger = logging.getLogger("traffic.scheduler")


class FleetScheduler:
    def __init__(
        self,
        w1: float | None = None,
        w2: float | None = None,
        d_max: float | None = None,
        min_battery: float | None = None,
    ):
        self.w1 = float(getattr(settings, "ACS_DISPATCH_W1_DISTANCE", 0.7) if w1 is None else w1)
        self.w2 = float(getattr(settings, "ACS_DISPATCH_W2_BATTERY", 0.3) if w2 is None else w2)
        self.d_max = max(
            1e-6,
            float(getattr(settings, "ACS_D_MAX_MAP", 100.0) if d_max is None else d_max),
        )
        self.min_battery = float(
            getattr(settings, "ACS_MIN_BATTERY_PERCENT", 25.0)
            if min_battery is None
            else min_battery
        )

    # -- cost -------------------------------------------------------------
    def _distance_lookup(self, robots: list[RobotSnapshot]):
        graph = get_graph()
        cache: dict[str, dict[str, float]] = {}
        for robot in robots:
            if robot.current_node:
                cache[robot.serial_number] = distances_from(robot.current_node, graph)
        return graph, cache

    def _cost(
        self, task: FleetTask, robot: RobotSnapshot, distances: dict[str, float] | None
    ) -> float:
        if not robot.available or distances is None:
            return BIG_COST
        if robot.battery_level < self.min_battery:
            return BIG_COST
        distance = distances.get(task.start_node, INF)
        if distance == INF:
            return BIG_COST
        normalized_distance = min(1.0, distance / self.d_max)
        battery_term = max(0.0, 1.0 - (float(robot.battery_level) / 100.0))
        return self.w1 * normalized_distance + self.w2 * battery_term

    def build_cost_matrix(
        self,
        tasks: list[FleetTask],
        robots: list[RobotSnapshot],
        graph=None,
        distance_cache=None,
    ) -> list[list[float]]:
        if not tasks or not robots:
            return []
        if distance_cache is None:
            _graph, distance_cache = self._distance_lookup(robots)
        matrix: list[list[float]] = []
        for row, task in enumerate(tasks):
            row_costs = []
            for column, robot in enumerate(robots):
                cost = self._cost(task, robot, distance_cache.get(robot.serial_number))
                if cost < BIG_COST:
                    # epsilon nhỏ đảm bảo kết quả tất định khi chi phí bằng nhau
                    cost += row * 1e-6 + column * 1e-9
                row_costs.append(cost)
            matrix.append(row_costs)
        return matrix

    # -- scheduling -------------------------------------------------------
    def _assign_tier(self, tasks: list[FleetTask], robots: list[RobotSnapshot], strategy: str):
        if not tasks or not robots:
            return [], [], strategy
        graph, cache = self._distance_lookup(robots)
        matrix = self.build_cost_matrix(tasks, robots, graph, cache)
        pairs, _total, solver = solve_assignment(matrix)
        strategy_used = solver
        if not pairs and matrix:
            pairs = greedy_assignment(matrix)
            strategy_used = "greedy-fallback"
        assignments = []
        for row, column in pairs:
            task = tasks[row]
            robot = robots[column]
            assignments.append(
                FleetAssignment(
                    task=task,
                    amr_serial_number=robot.serial_number,
                    cost=matrix[row][column],
                    distance=(cache.get(robot.serial_number) or {}).get(task.start_node, INF),
                )
            )
        return assignments, matrix, strategy_used

    def schedule(self, tasks: list[FleetTask], robots: list[RobotSnapshot]) -> FleetScheduleResult:
        """Gán theo tầng ưu tiên (cao trước), tie-break theo SLA và task_id."""
        result = FleetScheduleResult(strategy="tiered")
        remaining_tasks = sorted(
            tasks,
            key=lambda task: (
                -int(RobotPriority.coerce(task.priority)),
                -task.sla_urgency(),
                task.task_id,
            ),
        )
        available = [robot for robot in robots if robot.available]
        if not remaining_tasks or not available:
            result.unassigned = list(remaining_tasks)
            return result

        strategies: list[str] = []
        priorities = sorted(
            {int(RobotPriority.coerce(task.priority)) for task in remaining_tasks}, reverse=True
        )
        for priority in priorities:
            tier = [
                task
                for task in remaining_tasks
                if int(RobotPriority.coerce(task.priority)) == priority
            ]
            if not tier or not available:
                continue
            assignments, matrix, strategy_used = self._assign_tier(tier, available, "tiered")
            strategies.append(strategy_used)
            if matrix and not result.cost_matrix:
                result.cost_matrix = matrix
            assigned_serials = {
                item.amr_serial_number for item in assignments if item.assigned
            }
            result.assignments.extend(assignments)
            available = [
                robot for robot in available if robot.serial_number not in assigned_serials
            ]

        assigned_task_ids = {
            item.task.task_id for item in result.assignments if item.assigned
        }
        result.unassigned = [
            task for task in remaining_tasks if task.task_id not in assigned_task_ids
        ]
        if strategies:
            solver = "scipy" if any("scipy" in item for item in strategies) else "builtin"
            if any(item.startswith("greedy") for item in strategies):
                solver = "greedy-fallback"
            result.strategy = f"{solver}+tiered"
        return result

    def schedule_global(
        self, tasks: list[FleetTask], robots: list[RobotSnapshot]
    ) -> FleetScheduleResult:
        """Gán toàn cục không phân tầng (tối ưu tổng quãng đường)."""
        result = FleetScheduleResult()
        available = [robot for robot in robots if robot.available]
        if not tasks or not available:
            result.unassigned = list(tasks)
            result.strategy = "noop"
            return result
        assignments, matrix, strategy_used = self._assign_tier(
            list(tasks), available, "global"
        )
        result.assignments = assignments
        result.cost_matrix = matrix
        result.strategy = strategy_used
        assigned_ids = {item.task.task_id for item in assignments if item.assigned}
        result.unassigned = [task for task in tasks if task.task_id not in assigned_ids]
        return result

