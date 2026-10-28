"""VDA 5050 - đóng gói bản tin (order, instantActions)."""

from acs.vda5050.action import build_instant_action_payload
from acs.vda5050.order import build_order_payload

__all__ = ["build_order_payload", "build_instant_action_payload"]
