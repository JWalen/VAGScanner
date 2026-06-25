"""Rule-based diagnostic engine.

Turns an Auto-Scan and/or a measuring log into a prioritized list of findings
— each with a plain-English explanation and likely causes — by combining the
fault-code knowledge base with data-driven symptom heuristics (lean/rich fuel
trims, overheating, boost shortfall, rising misfire counters, heat soak).

Standard-library only. This is the shared brain behind the GUI Diagnosis panel,
the MCP ``diagnose`` tool and the report generator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from . import knowledge
from ._dtc_data import SEVERITY_ORDER
from .parse import AutoScan, MeasuringLog

# Standard OBD code embedded in a VCDS fault's status-detail line (e.g. "P2196").
_PCODE_RE = re.compile(r"\b([PUBC][0-9]{4})\b")


@dataclass
class Finding:
    severity: str  # critical | high | medium | low | info
    title: str
    detail: str
    category: str  # "fault" | "data"
    causes: List[str] = field(default_factory=list)
    evidence: Optional[str] = None
    code: Optional[str] = None

    @property
    def severity_rank(self) -> int:
        return SEVERITY_ORDER.get(self.severity, 0)


@dataclass
class DiagnosticReport:
    vin: Optional[str]
    mileage: Optional[str]
    findings: List[Finding] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def summary(self) -> dict:
        counts = {s: 0 for s in SEVERITY_ORDER}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return counts

    @property
    def headline(self) -> str:
        if not self.findings:
            return "No faults or abnormal readings detected."
        worst = self.findings[0]
        n = len(self.findings)
        return f"{n} finding(s); most severe: {worst.severity.upper()} — {worst.title}"


def _fault_findings(scan: AutoScan) -> List[Finding]:
    out: List[Finding] = []
    for module in scan.modules:
        for fault in module.faults:
            # VCDS lists a VAG numeric code with the standard OBD code in the
            # status detail; look up the standard code (our DB is keyed by it).
            pcode = None
            for src in (fault.status_detail or "", fault.code or ""):
                m = _PCODE_RE.search(src)
                if m:
                    pcode = m.group(1)
                    break

            k = None
            for cand in (pcode, fault.code):
                if cand:
                    kk = knowledge.lookup(cand)
                    if kk.known:
                        k = kk
                        break
            if k is None:
                k = knowledge.lookup(pcode or fault.code)

            # Prefer VCDS's own description for the title; keep both codes.
            description = fault.description or k.description
            code_label = fault.code
            if pcode and pcode != fault.code:
                code_label = f"{fault.code} / {pcode}"

            detail = f"Module {module.address} ({module.name})."
            if fault.status_detail:
                detail += f" Status: {fault.status_detail}."
            if k.notes:
                detail += f" {k.notes}"

            out.append(
                Finding(
                    severity=k.severity,
                    title=f"{code_label} — {description}",
                    detail=detail,
                    category="fault",
                    causes=k.causes,
                    evidence=fault.status_detail,
                    code=k.code,
                )
            )
    return out


def _chan_max(log: MeasuringLog, *substrings):
    for s in substrings:
        ch = log.channel(s)
        if ch is not None and ch.max is not None:
            return ch
    return None


def _data_findings(log: MeasuringLog) -> List[Finding]:
    out: List[Finding] = []

    # --- fuel trims: lean / rich ------------------------------------------- #
    ltft = log.channel("Long Fuel Trim")
    if ltft is not None and ltft.max is not None and ltft.min is not None:
        if ltft.max > 10:
            sev = "high" if ltft.max > 20 else "medium"
            detail = (
                f"Long-term fuel trim reached +{ltft.max:.0f}% — the ECU is adding fuel to "
                "compensate for a lean condition. " + (knowledge.known_issue("pcv_failure") or "")
            )
            out.append(Finding(sev, "Lean fuel trims", detail, "data",
                               ["Intake/vacuum leak", "Failing PCV / crankcase breather",
                                "Dirty or failed MAF", "Low fuel pressure"],
                               evidence=f"LTFT max +{ltft.max:.0f}%"))
        elif ltft.min < -10:
            sev = "high" if ltft.min < -20 else "medium"
            out.append(Finding(sev, "Rich fuel trims",
                               f"Long-term fuel trim fell to {ltft.min:.0f}% — the ECU is pulling "
                               "fuel to compensate for a rich condition.", "data",
                               ["Leaking injector", "High fuel pressure", "Failed MAF",
                                "Faulty O2 sensor"],
                               evidence=f"LTFT min {ltft.min:.0f}%"))

    # --- coolant overheating ----------------------------------------------- #
    ect = _chan_max(log, "Coolant")
    if ect is not None and ect.max > 105:
        sev = "critical" if ect.max > 115 else "high"
        out.append(Finding(sev, "High coolant temperature",
                           f"Coolant temperature peaked at {ect.max:.0f}{_u(ect)}.", "data",
                           ["Failed thermostat", "Low coolant / air pocket", "Failed water pump",
                            "Radiator or cooling-fan fault"],
                           evidence=f"max {ect.max:.0f}{_u(ect)}"))

    # --- target vs actual shortfall (boost, etc.) -------------------------- #
    div = _divergence_finding(log)
    if div is not None:
        out.append(div)

    # --- rising misfire / fault counters ----------------------------------- #
    for ch in log.channels:
        low = ch.name.lower()
        if not any(k in low for k in ("misfire", "fault counter", "counter")):
            continue
        s = log.raw_series.get(ch.name)
        if not s or len(s["value"]) < 2:
            continue
        vals = s["value"]
        if vals[-1] > vals[0] and all(vals[i + 1] >= vals[i] - 1e-9 for i in range(len(vals) - 1)):
            detail = (f"{ch.name} climbed from {vals[0]:.0f} to {vals[-1]:.0f}. "
                      + (knowledge.known_issue("carbon_buildup") or ""))
            out.append(Finding("high", "Misfire / fault counter increasing", detail, "data",
                               ["Worn spark plugs", "Failing ignition coil",
                                "Carbon build-up on intake valves", "Low fuel pressure"],
                               evidence=f"{ch.name}: {vals[0]:.0f} -> {vals[-1]:.0f}"))

    # --- intake air temperature / heat soak -------------------------------- #
    iat = _chan_max(log, "Intake Air Temp", "Intake Temp")
    if iat is not None and iat.max > 70:
        out.append(Finding("low", "High intake air temperature",
                           f"Intake air temperature reached {iat.max:.0f}{_u(iat)} (heat soak).",
                           "data", ["Heat soak after sustained load", "Intercooler efficiency",
                                    "Hot restart"],
                           evidence=f"max {iat.max:.0f}{_u(iat)}"))
    return out


def _divergence_finding(log: MeasuringLog) -> Optional[Finding]:
    spec = actual = None
    for c in log.channels:
        n = c.name.lower()
        if spec is None and any(k in n for k in ("specified", "requested", "target", "desired")):
            spec = c
        if actual is None and "actual" in n:
            actual = c
    if not (spec and actual):
        return None
    s = log.raw_series.get(spec.name)
    a = log.raw_series.get(actual.name)
    if not s or not a:
        return None
    n = min(len(s["value"]), len(a["value"]))
    if n == 0:
        return None
    worst, worst_t = 0.0, None
    for i in range(n):
        d = s["value"][i] - a["value"][i]
        if d > worst:
            worst, worst_t = d, s["time"][i]
    base = max((abs(x) for x in s["value"][:n]), default=0.0) or 1.0
    rel = worst / base
    if rel < 0.08:
        return None
    sev = "high" if rel > 0.15 else "medium"
    return Finding(
        sev, "Actual value falls short of target",
        f"{actual.name} fell {worst:.0f}{_u(actual)} below {spec.name} "
        f"(~{rel * 100:.0f}%) at t={worst_t:.1f}s. " + (knowledge.known_issue("diverter_valve") or ""),
        "data",
        ["Charge-pipe / boost leak", "Failed diverter (bypass) valve",
         "Faulty N75 boost-control valve", "Wastegate actuator", "Cracked intercooler"],
        evidence=f"shortfall {worst:.0f}{_u(actual)} (~{rel * 100:.0f}%)",
    )


def _u(ch) -> str:
    return f" {ch.unit}" if ch.unit else ""


def report_to_text(report: "DiagnosticReport", log: Optional[MeasuringLog] = None) -> str:
    """Render a report as a compact plain-text block (e.g. for an AI prompt)."""
    lines: List[str] = []
    if report.vin:
        lines.append(f"VIN: {report.vin}")
    if report.mileage:
        lines.append(f"Mileage: {report.mileage}")
    lines.append(report.headline)
    lines.append("")
    for f in report.findings:
        lines.append(f"- [{f.severity.upper()}] {f.title}")
        if f.detail:
            lines.append(f"    {f.detail}")
        if f.causes:
            lines.append("    Likely causes: " + "; ".join(f.causes))
    if log is not None and log.channels:
        lines.append("")
        lines.append("Channels logged (min/max/mean):")
        for c in log.channels:
            lines.append(
                f"  {c.name} [{c.unit}]: "
                f"{_num(c.min)} / {_num(c.max)} / {_num(c.mean)}"
            )
    return "\n".join(lines)


def _num(x) -> str:
    return "?" if x is None else (f"{x:.1f}" if isinstance(x, float) else str(x))


def diagnose(scan: Optional[AutoScan] = None, log: Optional[MeasuringLog] = None) -> DiagnosticReport:
    """Build a prioritized diagnostic report from a scan and/or measuring log.

    Args:
        scan: Optional parsed Auto-Scan.
        log: Optional parsed measuring log.

    Returns:
        A :class:`DiagnosticReport` with findings sorted most-severe first.
    """
    findings: List[Finding] = []
    if scan is not None:
        findings.extend(_fault_findings(scan))
    if log is not None:
        findings.extend(_data_findings(log))

    findings.sort(key=lambda f: (-f.severity_rank, f.category, f.title))

    report = DiagnosticReport(
        vin=scan.vin if scan else None,
        mileage=scan.mileage if scan else None,
        findings=findings,
    )
    if scan is None and log is None:
        report.notes.append("Nothing to diagnose: provide a scan and/or a measuring log.")
    return report
