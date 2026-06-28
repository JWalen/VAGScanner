"""Computed (derived) channels via a safe expression evaluator.

Lets the toolkit synthesize channels that aid troubleshooting — total fuel trim,
estimated AFR, derived boost — from the channels already in a log. Expressions
are evaluated with a restricted AST walker (no ``eval`` of arbitrary code), so a
user-supplied formula can never run code.

Standard-library only.
"""

from __future__ import annotations

import ast
import math
import operator
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .parse import Channel, MeasuringLog, _downsample, _fill_stats

_BIN = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {"abs": abs, "min": min, "max": max, "round": round}


def _eval_node(node, env: Dict[str, float]):
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        return _BIN[type(node.op)](_eval_node(node.left, env), _eval_node(node.right, env))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval_node(node.operand, env))
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        raise ValueError(f"unknown variable: {node.id}")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
        return _FUNCS[node.func.id](*[_eval_node(a, env) for a in node.args])
    raise ValueError("unsupported expression element")


def evaluate_expression(expr: str, variables: Dict[str, float]) -> float:
    """Safely evaluate an arithmetic ``expr`` using ``variables``.

    Supports + - * / ** %, unary +/-, numeric literals, the named variables and
    the functions abs/min/max/round. Anything else raises ``ValueError``.
    """
    tree = ast.parse(expr, mode="eval")
    val = float(_eval_node(tree.body, variables))
    if not math.isfinite(val):  # inf/NaN (e.g. divide-by-zero) breaks JSON + stats
        raise ValueError("non-finite result")
    return val


@dataclass
class ComputedDef:
    name: str
    unit: str
    expr: str
    inputs: Dict[str, str] = field(default_factory=dict)  # var -> channel-name substring


def standard_defs(log: MeasuringLog) -> List[ComputedDef]:
    """Build the standard computed-channel set applicable to ``log``."""
    defs: List[ComputedDef] = []
    stft = log.channel("Short Fuel Trim")
    ltft = log.channel("Long Fuel Trim")
    if stft and ltft:
        inp = {"stft": stft.name, "ltft": ltft.name}
        defs.append(ComputedDef("Fuel Trim Total", "%", "stft + ltft", dict(inp)))
        # Rough indicator: positive total trim => ECU compensating for a lean tendency.
        defs.append(
            ComputedDef("AFR (estimated)", "AFR", "14.7 / (1 + (stft + ltft) / 100)", dict(inp))
        )

    if not log.channel("Boost"):
        mapc = log.channel("MAP") or log.channel("Intake Manifold") or log.channel("Intake Pres")
        baro = log.channel("Barometric")
        if mapc and baro:
            defs.append(
                ComputedDef("Boost (derived)", mapc.unit, "manifold - ambient",
                            {"manifold": mapc.name, "ambient": baro.name})
            )
    return defs


def add_computed_channels(
    log: MeasuringLog,
    defs: Optional[List[ComputedDef]] = None,
    max_points: int = 2000,
) -> List[str]:
    """Append computed channels to ``log`` in place; return the names added.

    A definition is skipped when any input channel is missing or a channel of
    that name already exists.
    """
    if defs is None:
        defs = standard_defs(log)
    added: List[str] = []
    for d in defs:
        if log.channel(d.name) and log.channel(d.name).name == d.name:
            continue
        resolved = {var: log.channel(sub) for var, sub in d.inputs.items()}
        if any(c is None for c in resolved.values()):
            continue
        series = {var: log.raw_series[c.name] for var, c in resolved.items()}
        first_var = next(iter(d.inputs))
        taxis = series[first_var]["time"]
        n = min(len(series[v]["value"]) for v in d.inputs)

        times: List[float] = []
        vals: List[float] = []
        for i in range(n):
            env = {var: series[var]["value"][i] for var in d.inputs}
            if any(v is None for v in env.values()):
                continue
            try:
                r = evaluate_expression(d.expr, env)
            except (ValueError, ZeroDivisionError, ArithmeticError):
                continue
            times.append(taxis[i] if i < len(taxis) else float(i))
            vals.append(r)
        if not vals:
            continue

        ch = Channel(name=d.name, unit=d.unit, column_index=-1, time_column_index=None,
                     group="(computed)")
        _fill_stats(ch, vals)
        log.channels.append(ch)
        log.raw_series[d.name] = {"time": times, "value": vals}
        t_ds, v_ds = _downsample(times, vals, max_points)
        log.series[d.name] = {"time": t_ds, "value": v_ds, "unit": d.unit}
        added.append(d.name)
    return added
