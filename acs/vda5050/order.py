"""Đóng gói bản tin VDA 5050 ``order`` (chuẩn 2.0.0)."""

from datetime import datetime, timezone

from acs.models import Order
from map.models import Edge


def build_order_payload(order: Order, header_id: int = 1) -> dict:
    robot = order.robot
    order_nodes = list(order.order_nodes.select_related("node").prefetch_related("actions").all())

    nodes_payload = []
    edges_payload = []

    for idx, onode in enumerate(order_nodes):
        n = onode.node
        actions_list = [
            {
                "actionId": act.action_id,
                "actionType": act.action_type,
                "blockingType": act.blocking_type,
                "actionParameters": act.params,
            }
            for act in onode.actions.all()
        ]
        nodes_payload.append({
            "nodeId": n.node_id,
            "sequenceId": onode.sequence_id,
            "released": onode.released,
            "nodePosition": {
                "x": n.pos_x,
                "y": n.pos_y,
                "theta": n.theta,
                "mapId": n.map_topology_id,
            },
            "actions": actions_list,
        })

        # Sinh cạnh nối (Edge) mang sequenceId lẻ giữa nút hiện tại và nút kế tiếp
        if idx < len(order_nodes) - 1:
            next_onode = order_nodes[idx + 1]
            edge_obj = Edge.objects.filter(
                start_node_id=n.node_id, end_node_id=next_onode.node_id
            ).first()
            if not edge_obj:
                edge_obj = Edge.objects.filter(
                    start_node_id=next_onode.node_id, end_node_id=n.node_id, direction="BI"
                ).first()

            max_speed = edge_obj.max_speed if edge_obj else 1.0
            edge_id = edge_obj.edge_id if edge_obj else f"E_{n.node_id}_{next_onode.node_id}"

            edges_payload.append({
                "edgeId": edge_id,
                "sequenceId": onode.sequence_id + 1,
                "released": bool(onode.released and next_onode.released),
                "startNodeId": n.node_id,
                "endNodeId": next_onode.node_id,
                "maxSpeed": max_speed,
            })

    return {
        "headerId": header_id,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-4] + "Z",
        "version": "2.0.0",
        "manufacturer": robot.manufacturer,
        "serialNumber": robot.serial_number,
        "orderId": order.order_id,
        "orderUpdateId": order.order_update_id,
        "nodes": nodes_payload,
        "edges": edges_payload,
    }
