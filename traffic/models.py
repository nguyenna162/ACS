"""Kiểu dữ liệu điều phối (in-memory, không lưu DB) theo tài liệu dự án.

Tài liệu: ``traffic/models.py`` chứa ``FleetTask``, ``FleetAssignment``,
``FleetScheduleResult`` (dataclass), ``RobotPriority`` (IntEnum) và
``ReservationStatus`` (Enum) - các cấu trúc dùng trong bộ lập lịch.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum, IntEnum


class RobotPriority(IntEnum):
    """Mức ưu tiên robot/nhiệm vụ, số lớn hơn được phục vụ trước."""

    LOW = 10
    NORMAL = 50
    HIGH = 80
    URGENT = 100

    @classmethod
    def coerce(cls, value) -> "RobotPriority":
        if isinstance(value, RobotPriority):
            return value
        if isinstance(value, (int, float)):
            numeric = int(value)
            if numeric >= cls.URGENT:
                return cls.URGENT
            if numeric >= cls.HIGH:
                return cls.HIGH
            if numeric >= cls.NORMAL:
                return cls.NORMAL
            if numeric > 0:
                return cls.LOW
            return cls.NORMAL
        text = str(value or "").strip().upper()
        return cls.__members__.get(text, cls.NORMAL)


class ReservationStatus(str, Enum):
    FREE = "FREE"
    RESERVED = "RESERVED"
    OCCUPIED = "OCCUPIED"
    RELEASED = "RELEASED"


class TaskType(str, Enum):
    SUPPLY = "SUPPLY"
    TRANSFER = "TRANSFER"
    CHARGE = "CHARGE"
    PARK = "PARK"
    RETURN = "RETURN"


@dataclass
class FleetTask:
    """Một nhiệm vụ cần gán cho AMR (in-memory view của Order/SubOrder)."""

    task_id: str
    start_node: str
    end_node: str
    priority: int = int(RobotPriority.NORMAL)
    task_type: str = TaskType.TRANSFER.value
    sla_deadline: dt.datetime | None = None
    payload: dict = field(default_factory=dict)

    @property
    def priority_enum(self) -> RobotPriority:
        return RobotPriority.coerce(self.priority)

    def sla_urgency(self, now: dt.datetime | None = None) -> float:
        """0..1, càng gần hạn càng cao (dùng để tie-break)."""
        if not self.sla_deadline:
            return 0.0
        now = now or dt.datetime.now(dt.timezone.utc)
        remaining = (self.sla_deadline - now).total_seconds()
        if remaining <= 0:
            return 1.0
        return max(0.0, min(1.0, 1.0 - remaining / 1800.0))


@dataclass
class FleetAssignment:
    task: FleetTask
    amr_serial_number: str | None
    cost: float = float("inf")
    distance: float = float("inf")
    reason: str = ""

    @property
    def assigned(self) -> bool:
        return self.amr_serial_number is not None

    def as_dict(self) -> dict:
        return {
            "task_id": self.task.task_id,
            "amr_serial_number": self.amr_serial_number,
            "cost": None if self.cost == float("inf") else round(self.cost, 4),
            "distance": None if self.distance == float("inf") else round(self.distance, 2),
            "assigned": self.assigned,
            "reason": self.reason,
            "priority": int(self.task.priority_enum),
        }


@dataclass
class FleetScheduleResult:
    assignments: list[FleetAssignment] = field(default_factory=list)
    unassigned: list[FleetTask] = field(default_factory=list)
    strategy: str = "hungarian"
    cost_matrix: list[list[float]] = field(default_factory=list)

    @property
    def assigned_count(self) -> int:
        return sum(1 for item in self.assignments if item.assigned)

    def as_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "assigned": self.assigned_count,
            "unassigned": len(self.unassigned),
            "assignments": [item.as_dict() for item in self.assignments],
            "unassigned_tasks": [task.task_id for task in self.unassigned],
        }


@dataclass
class RobotSnapshot:
    """Ảnh chụp robot tại thời điểm lập lịch."""

    serial_number: str
    current_node: str | None
    battery_level: float = 100.0
    operational_state: str = "IDLE"
    is_idle: bool = True

    @property
    def available(self) -> bool:
        return (
            self.current_node is not None
            and self.operational_state not in ("OFFLINE", "ERROR", "INIT")
        )
