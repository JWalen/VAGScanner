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
from .profiles import Profile, get_profile

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


def _fault_findings(scan: AutoScan, profile: Profile) -> List[Finding]:
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
                    kk = knowledge.lookup(cand, brand=profile.id)
                    if kk.known:
                        k = kk
                        break
            if k is None:
                k = knowledge.lookup(pcode or fault.code, brand=profile.id)

            # Prefer VCDS's own description for the title; keep both codes.
            description = fault.description or k.description
            code_label = fault.code
            if pcode and pcode != fault.code:
                code_label = f"{fault.code} / {pcode}"

            detail = f"Module {module.address} ({module.name})."
            if fault.status_detail:
                detail += f" Status: {fault.status_detail}."
            if k.notes and profile.code_notes:  # bundled notes are VAG-flavored
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


# Cam-to-crank correlation / engine-position codes — the classic signature of a
# stretched timing chain (or a jumped/worn belt). Brand variants resolve to these.
_TIMING_CODES = {"P0008", "P0009", "P0016", "P0017", "P0018", "P0019"}


def _timing_findings(scan: Optional[AutoScan], log: Optional[MeasuringLog],
                     profile: Optional[Profile] = None) -> List[Finding]:
    """Dedicated timing chain/belt stretch check (correlation DTCs + cam deviation)."""
    out: List[Finding] = []
    brand_note = (profile.known_issues.get("timing_chain", "") if profile else "")

    found = set()
    if scan is not None:
        for module in scan.modules:
            for fault in module.faults:
                for src in (fault.code or "", fault.status_detail or ""):
                    m = _PCODE_RE.search(src)
                    if m and m.group(1).upper() in _TIMING_CODES:
                        found.add(m.group(1).upper())
    if found:
        out.append(Finding(
            "high", "Possible timing chain / belt stretch",
            "Cam-to-crank correlation fault(s) stored (" + ", ".join(sorted(found)) + "). "
            "This is the classic signature of a stretched/worn timing chain (with worn guides or a "
            "weak tensioner) or a jumped/worn timing belt. A failed VVT actuator/phaser or a cam/"
            "crank sensor can mimic it — confirm by comparing actual-vs-specified camshaft timing "
            "and inspecting the chain stretch/tensioner. " + brand_note,
            "fault",
            ["Stretched timing chain + worn guides/tensioner", "Jumped or worn timing belt",
             "Failed VVT actuator/phaser or solenoid", "Cam/crank position sensor or reluctor ring"],
            evidence=", ".join(sorted(found)), code=sorted(found)[0]))

    # Measuring-log: a large camshaft-timing deviation points the same way.
    # Two ways VCDS/OBD logs expose it: an explicit deviation channel, OR a
    # specified-vs-actual camshaft-timing pair (how VAG 2.0 TSI/FSI and 3.0T show
    # chain stretch — the actual angle can't follow the specified).
    if log is not None:
        cam_dev = None  # (degrees, source-label, unit-suffix)

        for ch in log.channels:
            n = ch.name.lower()
            if "cam" in n and any(k in n for k in ("deviation", "timing error", "correlation",
                                                   "angle error", "offset")):
                d = max(abs(ch.max or 0.0), abs(ch.min or 0.0))
                if cam_dev is None or d > cam_dev[0]:
                    cam_dev = (d, ch.name, _u(ch))

        cam_spec = cam_act = None
        for c in log.channels:
            n = c.name.lower()
            if "cam" not in n:
                continue
            if cam_spec is None and any(k in n for k in ("spec", "target", "nominal",
                                                         "setpoint", "desired")):
                cam_spec = c
            if cam_act is None and any(k in n for k in ("actual", "act.", "real", "measured")):
                cam_act = c
        if cam_spec is not None and cam_act is not None:
            worst = _max_abs_diff(log, cam_spec.name, cam_act.name)
            if worst is not None and (cam_dev is None or worst > cam_dev[0]):
                cam_dev = (worst, f"{cam_act.name} vs {cam_spec.name}", _u(cam_act))

        if cam_dev is not None and cam_dev[0] >= 6.0:
            out.append(Finding(
                "high", "Camshaft timing deviation high",
                f"Camshaft timing deviation up to {cam_dev[0]:.1f}{cam_dev[2]} ({cam_dev[1]}). "
                "A large cam-timing deviation points to a stretched timing chain or a worn/weak "
                "chain tensioner (on VAG 2.0 TSI/FSI and the 3.0T this is the classic stretch "
                "signature) — also check the VVT actuator and oil supply/pressure to the cam "
                "phaser. Applies to any cam-phased engine.", "data",
                ["Stretched timing chain + worn guides/tensioner", "Worn / weak chain tensioner",
                 "VVT actuator / cam adjuster", "Low oil pressure to the cam phaser"],
                evidence=f"{cam_dev[1]}: up to {cam_dev[0]:.1f}{cam_dev[2]}"))
    return out


def _max_abs_diff(log: MeasuringLog, a_name: str, b_name: str):
    """Largest |a-b| across time-aligned samples of two channels (or None)."""
    a = log.raw_series.get(a_name)
    b = log.raw_series.get(b_name)
    if not a or not b:
        return None
    n = min(len(a["value"]), len(b["value"]))
    worst = 0.0
    for i in range(n):
        av, bv = a["value"][i], b["value"][i]
        if av is None or bv is None:
            continue
        worst = max(worst, abs(av - bv))
    return worst


def _chan_max(log: MeasuringLog, *substrings):
    for s in substrings:
        ch = log.channel(s)
        if ch is not None and ch.max is not None:
            return ch
    return None


def _data_findings(log: MeasuringLog, profile: Profile) -> List[Finding]:
    issues = profile.known_issues
    out: List[Finding] = []

    # --- fuel trims: lean / rich ------------------------------------------- #
    ltft = log.channel("Long Fuel Trim")
    if ltft is not None and ltft.max is not None and ltft.min is not None:
        if ltft.max > 10:
            sev = "high" if ltft.max > 20 else "medium"
            detail = (
                f"Long-term fuel trim reached +{ltft.max:.0f}% — the ECU is adding fuel to "
                "compensate for a lean condition. " + issues.get("pcv_failure", "")
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
    div = _divergence_finding(log, issues)
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
                      + issues.get("carbon_buildup", ""))
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


def _divergence_finding(log: MeasuringLog, issues: dict) -> Optional[Finding]:
    # Boost/charge-pressure target-vs-actual. Camshaft spec/actual pairs are
    # handled by the dedicated timing check, so skip "cam" channels here.
    spec = actual = None
    for c in log.channels:
        n = c.name.lower()
        if "cam" in n:
            continue
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
        f"(~{rel * 100:.0f}%) at t={worst_t:.1f}s. " + issues.get("diverter_valve", ""),
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


def diagnose(scan: Optional[AutoScan] = None, log: Optional[MeasuringLog] = None,
             profile="vag") -> DiagnosticReport:
    """Build a prioritized diagnostic report from a scan and/or measuring log.

    Args:
        scan: Optional parsed Auto-Scan.
        log: Optional parsed measuring log.
        profile: Vehicle/brand profile id (e.g. "vag", "ford", "generic") or a
            Profile — selects brand-specific known-issue notes.

    Returns:
        A :class:`DiagnosticReport` with findings sorted most-severe first.
    """
    prof = get_profile(profile)
    findings: List[Finding] = []
    if scan is not None:
        findings.extend(_fault_findings(scan, prof))
    if log is not None:
        findings.extend(_data_findings(log, prof))
    findings.extend(_timing_findings(scan, log, prof))  # timing chain/belt stretch check

    findings.sort(key=lambda f: (-f.severity_rank, f.category, f.title))

    report = DiagnosticReport(
        vin=scan.vin if scan else None,
        mileage=scan.mileage if scan else None,
        findings=findings,
    )
    if scan is None and log is None:
        report.notes.append("Nothing to diagnose: provide a scan and/or a measuring log.")
    return report
