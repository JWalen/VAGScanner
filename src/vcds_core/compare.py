"""Compare two measuring logs channel-by-channel (before/after, pull-vs-pull).

Dependency-free and testable: produces a structured per-channel diff that the
GUI renders as a table and uses to overlay the second log on the plot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .parse import MeasuringLog


@dataclass
class ChannelDiff:
    name: str
    unit: str
    in_a: bool
    in_b: bool
    a_min: Optional[float] = None
    a_max: Optional[float] = None
    a_mean: Optional[float] = None
    b_min: Optional[float] = None
    b_max: Optional[float] = None
    b_mean: Optional[float] = None

    @property
    def d_mean(self) -> Optional[float]:
        if self.a_mean is None or self.b_mean is None:
            return None
        return self.b_mean - self.a_mean

    @property
    def d_max(self) -> Optional[float]:
        if self.a_max is None or self.b_max is None:
            return None
        return self.b_max - self.a_max


@dataclass
class LogComparison:
    a_name: str
    b_name: str
    channels: List[ChannelDiff]


def compare_logs(a: MeasuringLog, b: MeasuringLog, a_name: str = "A", b_name: str = "B") -> LogComparison:
    """Build a per-channel comparison of two parsed logs (union of channels)."""
    a_by = {c.name: c for c in a.channels}
    b_by = {c.name: c for c in b.channels}

    order: List[str] = [c.name for c in a.channels]
    for c in b.channels:
        if c.name not in a_by:
            order.append(c.name)

    diffs: List[ChannelDiff] = []
    for name in order:
        ca = a_by.get(name)
        cb = b_by.get(name)
        diffs.append(ChannelDiff(
            name=name,
            unit=(ca.unit if ca else (cb.unit if cb else "")),
            in_a=ca is not None,
            in_b=cb is not None,
            a_min=ca.min if ca else None, a_max=ca.max if ca else None, a_mean=ca.mean if ca else None,
            b_min=cb.min if cb else None, b_max=cb.max if cb else None, b_mean=cb.mean if cb else None,
        ))
    return LogComparison(a_name=a_name, b_name=b_name, channels=diffs)
