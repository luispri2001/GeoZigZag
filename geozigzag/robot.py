"""Vehicle parameters used by geometric coverage planning."""

import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RobotProfile:
    kind: str = "differential"
    clearance_m: float = 0.4
    headland_m: float = 2.0
    wheelbase_m: float = 1.0
    max_steering_deg: float = 35.0

    def __post_init__(self):
        if self.kind not in {"differential", "skid_steer", "ackermann"}:
            raise ValueError(
                "Unknown robot type; choose differential, skid_steer or ackermann."
            )
        for name in ("clearance_m", "headland_m", "wheelbase_m", "max_steering_deg"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"{name} must be finite.")
        if (
            not 0.1 <= self.clearance_m <= 50
            or not 0 <= self.headland_m <= 500
            or not 0.1 <= self.wheelbase_m <= 30
        ):
            raise ValueError(
                "Clearance must be 0.1–50 m, headland 0–500 m and wheelbase 0.1–30 m."
            )
        if not 1 <= self.max_steering_deg <= 84:
            raise ValueError("Steering angle must be between 1 and 84 degrees.")

    @property
    def minimum_radius_m(self):
        return (
            self.wheelbase_m / math.tan(math.radians(self.max_steering_deg))
            if self.kind == "ackermann"
            else 0.0
        )

    def metadata(self):
        return {
            **asdict(self),
            "minimum_radius_m": self.minimum_radius_m,
            "strategy": "forward_dubins"
            if self.kind == "ackermann"
            else "stop_rotate_translate",
        }
