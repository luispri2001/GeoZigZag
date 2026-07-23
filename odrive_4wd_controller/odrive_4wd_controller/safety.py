"""Fault-state machine and command watchdog."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum


class DriveState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    INITIALIZING = "INITIALIZING"
    IDLE = "IDLE"
    READY = "READY"
    ENABLED = "ENABLED"
    STOPPING = "STOPPING"
    FAULT = "FAULT"
    EMERGENCY_STOP = "EMERGENCY_STOP"


@dataclass
class FaultRecord:
    code: str
    message: str
    timestamp: float
    recoverable: bool = False


@dataclass
class SafetyMachine:
    state: DriveState = DriveState.DISCONNECTED
    fault: FaultRecord | None = None
    history: list[FaultRecord] = field(default_factory=list)

    def transition(self, target: DriveState) -> None:
        if self.state in (DriveState.FAULT, DriveState.EMERGENCY_STOP) and target not in (
            DriveState.IDLE,
            DriveState.DISCONNECTED,
        ):
            raise RuntimeError(f"cannot transition from {self.state} to {target}")
        self.state = target

    def trip(self, code: str, message: str, timestamp: float, *, recoverable: bool = False) -> None:
        record = FaultRecord(code, message, timestamp, recoverable)
        self.fault = record
        self.history.append(record)
        self.state = DriveState.FAULT

    def emergency_stop(self, timestamp: float, message: str = "operator emergency stop") -> None:
        record = FaultRecord("EMERGENCY_STOP", message, timestamp, False)
        self.fault = record
        self.history.append(record)
        self.state = DriveState.EMERGENCY_STOP

    def clear_recoverable(self) -> bool:
        if self.fault is None:
            return True
        if not self.fault.recoverable:
            return False
        self.fault = None
        self.state = DriveState.IDLE
        return True


@dataclass
class CommandWatchdog:
    timeout_s: float
    last_command_time: float | None = None

    def feed(self, now: float) -> None:
        if not math.isfinite(now):
            raise ValueError("watchdog time must be finite")
        self.last_command_time = now

    def stale(self, now: float) -> bool:
        if self.last_command_time is None:
            return True
        age = now - self.last_command_time
        return age > self.timeout_s and not math.isclose(
            age, self.timeout_s, rel_tol=1e-9, abs_tol=1e-12
        )
