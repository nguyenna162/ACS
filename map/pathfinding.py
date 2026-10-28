import heapq
import math
from typing import List, Tuple, Optional
from map.models import Node, Edge

def euclidean_distance(n1: Node, n2: Node) -> float:
    return math.hypot(n1.pos_x - n2.pos_x, n1.pos_y - n2.pos_y)

def a_star_search(start_node_id: str, goal_node_id: str, blocked_nodes: set = None) -> Tuple[Optional[List[str]], float]:
    """
    Trả về (Danh sách node_id theo lộ trình ngắn nhất, tổng khoảng cách)
    """
    blocked_nodes = blocked_nodes or set()
    nodes = {n.node_id: n for n in Node.objects.all()}
    if start_node_id not in nodes or goal_node_id not in nodes:
        return None, float('inf')

    if start_node_id == goal_node_id:
        return [start_node_id], 0.0

    adj = {n_id: [] for n_id in nodes}
    for edge in Edge.objects.select_related('start_node', 'end_node').all():
        if edge.start_node_id in adj:
            adj[edge.start_node_id].append((edge.end_node_id, edge.distance))
        if edge.direction == 'BI' and edge.end_node_id in adj:
            adj[edge.end_node_id].append((edge.start_node_id, edge.distance))

    open_set = [(0.0, start_node_id)]
    came_from = {}
    g_score = {n_id: float('inf') for n_id in nodes}
    g_score[start_node_id] = 0.0

    while open_set:
        _, current = heapq.heappop(open_set)

        if current == goal_node_id:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path, g_score[goal_node_id]

        for neighbor, weight in adj.get(current, []):
            if neighbor in blocked_nodes and neighbor != goal_node_id:
                continue
            tentative_g = g_score[current] + weight
            if tentative_g < g_score[neighbor]:
                came_from[neighbor] = current
                g_score[neighbor] = tentative_g
                f_score = tentative_g + euclidean_distance(nodes[neighbor], nodes[goal_node_id])
                heapq.heappush(open_set, (f_score, neighbor))

    return None, float('inf')
