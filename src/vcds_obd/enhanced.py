"""Manufacturer-enhanced PIDs (UDS service $22 / "mode 22") — EXPERIMENTAL.

Standard OBD-II (mode 01) only exposes generic PIDs. Each manufacturer also has
enhanced PIDs read via service $22 (e.g. what FORScan reads on Fords). This
module provides the framework: a user-editable PID library (name, 16-bit DID,
unit and a SAFE formula over the response data bytes ``a, b, c, …``) and a query
path over the ELM327.

IMPORTANT: enhanced DIDs and their formulas are vehicle/model-specific and are
NOT validated here. Treat the bundled entries as examples and verify each
against your vehicle (e.g. against FORScan community PID lists) before trusting
the numbers. Service $22 is read-only, so querying is safe even if a DID is
wrong (you just get no/garbage data).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from vcds_core.compute import evaluate_expression


@dataclass
class EnhancedPid:
    name: str
    unit: str
    brand: str
    did: str           # 16-bit identifier as hex, e.g. "1E1C"
    formula: str       # safe expression over data bytes a, b, c, … (ints 0-255)
    note: str = ""

    def request_hex(self) -> str:
        return "22" + self.did.strip().upper()


# Bundled EXAMPLES — verify DID + formula on your own vehicle before trusting.
DEFAULT_LIBRARY: List[EnhancedPid] = [
    EnhancedPid(
        "Transmission Fluid Temp (example)", "°C", "ford", "1E1C", "(a*256 + b)/16 - 40",
        note="EXAMPLE Ford DID/formula — verify against your vehicle (FORScan lists).",
    ),
    EnhancedPid(
        "Engine Oil Temp (example)", "°C", "ford", "1446", "a - 40",
        note="EXAMPLE Ford DID/formula — verify against your vehicle.",
    ),
]


def evaluate(pid: EnhancedPid, data_bytes: List[int]) -> Optional[float]:
    """Evaluate a PID's formula over response data bytes (a=byte0, b=byte1, …)."""
    if not data_bytes:
        return None
    env = {chr(ord("a") + i): int(b) for i, b in enumerate(data_bytes[:8])}
    try:
        return float(evaluate_expression(pid.formula, env))
    except (ValueError, ZeroDivisionError, ArithmeticError, KeyError):
        return None


def query_enhanced(conn, pid: EnhancedPid) -> Optional[float]:
    """Query an enhanced PID via ``conn.query_raw`` and decode it.

    ``conn.query_raw(request_hex)`` must return the response data bytes (after
    the ``62`` + echoed DID), or an empty list.
    """
    reader = getattr(conn, "query_raw", None)
    if not callable(reader):
        return None
    try:
        data = reader(pid.request_hex())
    except Exception:  # noqa: BLE001
        return None
    return evaluate(pid, list(data) if data else [])


def load_library(path: str) -> List[EnhancedPid]:
    """Load the PID library from JSON, or the bundled defaults if absent/invalid."""
    if not path or not os.path.isfile(path):
        return [EnhancedPid(**asdict(p)) for p in DEFAULT_LIBRARY]
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return [EnhancedPid(**d) for d in data]
    except Exception:  # noqa: BLE001
        return [EnhancedPid(**asdict(p)) for p in DEFAULT_LIBRARY]


def save_library(path: str, pids: List[EnhancedPid]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([asdict(p) for p in pids], fh, indent=2)


def for_brand(pids: List[EnhancedPid], brand: str) -> List[EnhancedPid]:
    return [p for p in pids if p.brand == brand]
