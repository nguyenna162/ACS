"""Đồ thị bản đồ + Dijkstra có cache (theo tài liệu: graph_distance 60s TTL).

Dùng chung cho: cost matrix của FleetScheduler, ước lượng khoảng cách ghép
SupplyRequest và kiểm tra khả năng tiếp cận trước khi phát đơn.
"""

from __future__ import annotations

import heapq
import math
import threading
import time

from django.conf import settings

INF = float("inf")
_lock = threading.RLock()


def euclidean(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2 - x1, y2 - y1)


class TopologyGraph:
    """Đồ thị có trọng số dựng từ bảng map_node/map_edge."""

    def __init__(self):
        self.adjacency: dict[str, dict[str, float]] = {}
        self.coords: dict[str, tuple[float, float]] = {}
        self.loaded_at: float = 0.0

    def build(self) -> "TopologyGraph":
        from map.models import Node

        adjacency: dict[str, dict[str, float]] = {}
        coords: dict[str, tuple[float, float]] = {}
        for node in Node.objects.all().values("node_id", "pos_x", "pos_y"):
            adjacency[node["node_id"]] = {}
            coords[node["node_id"]] = (float(node["pos_x"]), float(node["pos_y"]))

        from map.models import Edge

        for edge in Edge.objects.all().values(
            "start_node_id", "end_node_id", "distance", "direction"
        ):
            start = edge["start_node_id"]
            end = edge["end_node_id"]
            if start not in adjacency or end not in adjacency:
                continue
            weight = float(edge["distance"] or 0.0)
            if weight <= 0:
                x1, y1 = coords[start]
                x2, y2 = coords[end]
                weight = max(euclidean(x1, y1, x2, y2), 0.001)
            if weight < adjacency[start].get(end, INF):
                adjacency[start][end] = weight
            if edge["direction"] == "BI" and weight < adjacency[end].get(start, INF):
                adjacency[end][start] = weight

        self.adjacency = adjacency
        self.coords = coords
        self.loaded_at = time.monotonic()
        return self

    def ttl(self) -> float:
        try:
            return max(1.0, float(settings.ACS_TOPOLOGY_CACHE_SECONDS))
        except Exception:  # pragma: no cover
            return 60.0

    def is_stale(self) -> bool:
        return (time.monotonic() - self.loaded_at) > self.ttl()

    def neighbors(self, node_id: str) -> dict[str, float]:
        return self.adjacency.get(node_id, {})

    def heuristic(self, node_id: str, goal: str) -> float:
        if node_id not in self.coords or goal not in self.coords:
            return 0.0
        x1, y1 = self.coords[node_id]
        x2, y2 = self.coords[goal]
        return euclidean(x1, y1, x2, y2)


_CACHE = TopologyGraph()


def get_graph(force: bool = False) -> TopologyGraph:
    global _CACHE
    with _lock:
        if force or _CACHE.is_stale() or not _CACHE.adjacency:
            _CACHE = TopologyGraph().build()
        return _CACHE


def invalidate_cache() -> None:
    global _CACHE
    with _lock:
        _CACHE = TopologyGraph()


def dijkstra(start: str, goal: str | None = None, graph: TopologyGraph | None = None):
    """Trả về (khoảng cách tới goal|dict, previous) - dừng sớm khi có goal."""
    graph = graph or get_graph()
    if start not in graph.adjacency:
        return (INF if goal else {}), {}
    distances: dict[str, float] = {start: 0.0}
    previous: dict[str, str] = {}
    heap: list[tuple[float, str]] = [(0.0, start)]
    visited: set[str] = set()

    while heap:
        current_distance, node = heapq.heappop(heap)
        if node in visited:
            continue
        visited.add(node)
        if goal is not None and node == goal:
            break
        for neighbor, weight in graph.neighbors(node).items():
            candidate = current_distance + weight
            if candidate < distances.get(neighbor, INF):
                distances[neighbor] = candidate
                previous[neighbor] = node
                heapq.heappush(heap, (candidate, neighbor))

    if goal is None:
        return distances, previous
    return distances.get(goal, INF), previous


def shortest_path(start: str, goal: str, graph: TopologyGraph | None = None):
    """Trả về (distance, [node_id...]) hoặc (inf, []) nếu không tới được."""
    if start == goal:
        return 0.0, [start]
    distance, previous = dijkstra(start, goal, graph)
    if distance == INF:
        return INF, []
    path = [goal]
    cursor = goal
    while cursor != start:
        cursor = previous.get(cursor)
        if cursor is None:
            return INF, []
        path.append(cursor)
    path.reverse()
    return distance, path


def distances_from(start: str, graph: TopologyGraph | None = None) -> dict[str, float]:
    distances, _ = dijkstra(start, None, graph)
    return distances


def graph_distance(start: str, goal: str, graph: TopologyGraph | None = None) -> float:
    return shortest_path(start, goal, graph)[0]


def path_distance(path: list[str], graph: TopologyGraph | None = None) -> float:
    if not path or len(path) < 2:
        return 0.0
    graph = graph or get_graph()
    total = 0.0
    for index in range(len(path) - 1):
        step = graph.neighbors(path[index]).get(path[index + 1])
        if step is None:
            fallback = graph_distance(path[index], path[index + 1], graph)
            if fallback == INF:
                return INF
            step = fallback
        total += step
    return total


def nearest_node(x: float, y: float, graph: TopologyGraph | None = None) -> str | None:
    graph = graph or get_graph()
    best = None
    best_distance = INF
    for node_id, (node_x, node_y) in graph.coords.items():
        distance = euclidean(x, y, node_x, node_y)
        if distance < best_distance:
            best_distance = distance
            best = node_id
    return best
