"""Đóng gói bản tin VDA 5050 ``instantActions`` (chuẩn 2.0.0)."""

from datetime import datetime, timezone


def build_instant_action_payload(robot, action_type: str, action_id: str) -> dict:
    """action_type: 'startPause' | 'stopPause' | 'cancelOrder'."""
    return {
        "headerId": 1,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-4] + "Z",
        "version": "2.0.0",
        "manufacturer": robot.manufacturer,
        "serialNumber": robot.serial_number,
        "actions": [
            {
                "actionId": action_id,
                "actionType": action_type,
                "blockingType": "HARD",
            }
        ],
    }
