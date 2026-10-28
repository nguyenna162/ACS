"""Test thuật toán điều phối: Hungarian, Dijkstra, lập lịch, đặt chỗ."""

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from map.models import Edge, MapTopology, Node
from amr.models import RobotProfile
from traffic.fleet_scheduler import FleetScheduler
from traffic.graph_distance import graph_distance, invalidate_cache, shortest_path
from traffic.hungarian import linear_sum_assignment, solve_assignment
from traffic.models import FleetTask, RobotPriority, RobotSnapshot
from traffic.state_reservation_manager import ReservationManager


class HungarianTests(SimpleTestCase):
    def test_optimal_square_assignment(self):
        cost = [[4, 1, 3], [2, 0, 5], [3, 2, 2]]
        rows, cols = linear_sum_assignment(cost)
        total = sum(cost[row][col] for row, col in zip(rows, cols))
        self.assertEqual(len(rows), 3)
        self.assertEqual(total, 5)

    def test_rectangular_more_robots_than_tasks(self):
        cost = [[9, 1, 5], [2, 8, 7]]
        rows, cols = linear_sum_assignment(cost)
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(cost[r][c] for r, c in zip(rows, cols)), 3)

    def test_infeasible_pairs_are_skipped(self):
        pairs, _total, strategy = solve_assignment([[1, 1e9], [1e9, 2]])
        self.assertIn(strategy, ("builtin", "scipy"))
        self.assertEqual(sorted(pairs), [(0, 0), (1, 1)])


class GraphBaseTestCase(TestCase):
    def setUp(self):
        invalidate_cache()
        self.topology = MapTopology.objects.create(
            map_id="MAP_T", map_name="Test", version="1.0.0", is_active=True
        )
        coords = {"A": (0, 0), "B": (10, 0), "C": (20, 0), "D": (10, 10)}
        self.nodes = {
            node_id: Node.objects.create(
                node_id=node_id, map_topology=self.topology, pos_x=x, pos_y=y
            )
            for node_id, (x, y) in coords.items()
        }
        for start, end, distance in (("A", "B", 10.0), ("B", "C", 10.0), ("B", "D", 30.0)):
            Edge.objects.create(
                edge_id=f"E_{start}_{end}",
                start_node=self.nodes[start],
                end_node=self.nodes[end],
                distance=distance,
                direction="BI",
            )
        invalidate_cache()

    def tearDown(self):
        invalidate_cache()


class GraphTests(GraphBaseTestCase):
    def test_shortest_path_and_distance(self):
        distance, path = shortest_path("A", "D")
        self.assertEqual(path, ["A", "B", "D"])
        self.assertEqual(distance, 40.0)
        self.assertEqual(graph_distance("C", "A"), 20.0)

    def test_unknown_node_returns_infinity(self):
        self.assertEqual(shortest_path("D", "D")[0], 0.0)
        distance, path = shortest_path("Z", "A")
        self.assertEqual(distance, float("inf"))
        self.assertEqual(path, [])


class FleetSchedulerTests(GraphBaseTestCase):
    def _robot(self, serial, node, battery=90.0, state="IDLE"):
        return RobotSnapshot(
            serial_number=serial,
            current_node=node,
            battery_level=battery,
            operational_state=state,
            is_idle=True,
        )

    def test_assigns_nearest_robot(self):
        tasks = [
            FleetTask(task_id="t1", start_node="A", end_node="C", priority=RobotPriority.HIGH),
            FleetTask(task_id="t2", start_node="C", end_node="A", priority=RobotPriority.NORMAL),
        ]
        robots = [self._robot("R1", "A"), self._robot("R2", "C")]
        result = FleetScheduler().schedule_global(tasks, robots)
        assignment = {item.task.task_id: item.amr_serial_number for item in result.assignments}
        self.assertEqual(assignment["t1"], "R1")
        self.assertEqual(assignment["t2"], "R2")

    def test_low_battery_robot_is_infeasible(self):
        tasks = [FleetTask(task_id="t1", start_node="A", end_node="C")]
        robots = [self._robot("R1", "A", battery=10.0)]
        result = FleetScheduler().schedule_global(tasks, robots)
        self.assertEqual(result.assigned_count, 0)
        self.assertEqual(len(result.unassigned), 1)

    def test_priority_tier_wins_over_distance(self):
        tasks = [
            FleetTask(task_id="far", start_node="C", end_node="A", priority=RobotPriority.URGENT),
            FleetTask(task_id="near", start_node="B", end_node="A", priority=RobotPriority.LOW),
        ]
        robots = [self._robot("R1", "B")]
        result = FleetScheduler().schedule(tasks, robots)
        assignment = {item.task.task_id: item.amr_serial_number for item in result.assignments}
        self.assertEqual(assignment.get("far"), "R1")
        self.assertEqual([task.task_id for task in result.unassigned], ["near"])


class ReservationTests(GraphBaseTestCase):
    def test_reserve_conflict_and_ttl_release(self):
        robot_a = RobotProfile.objects.create(
            serial_number="R_A", current_node=self.nodes["A"], operational_state="IDLE"
        )
        robot_b = RobotProfile.objects.create(
            serial_number="R_B", current_node=self.nodes["B"], operational_state="IDLE"
        )
        manager = ReservationManager(ttl_seconds=30)

        self.assertTrue(manager.reserve_node(robot_a, "A"))
        self.assertFalse(manager.reserve_node(robot_b, "A"))
        self.assertEqual(manager.node_status("A").value, "RESERVED")

        Node.objects.filter(node_id="A").update(
            locked_at=timezone.now() - timezone.timedelta(minutes=5)
        )
        self.assertGreaterEqual(manager.release_expired(), 1)
        self.assertEqual(manager.node_status("A").value, "FREE")
        self.assertTrue(manager.reserve_node(robot_b, "A"))

    def test_reserve_nodes_rolls_back_partial(self):
        robot = RobotProfile.objects.create(serial_number="R_C", operational_state="IDLE")
        blocker = RobotProfile.objects.create(serial_number="R_D", operational_state="IDLE")
        ReservationManager().reserve_node(blocker, "B")
        acquired = ReservationManager().reserve_nodes(robot, ["A", "B", "C"])
        self.assertEqual(acquired, [])
        self.assertFalse(Node.objects.get(pk="A").is_occupied)

