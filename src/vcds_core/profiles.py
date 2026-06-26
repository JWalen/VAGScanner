"""Vehicle/brand profiles.

A profile selects the brand-specific knowledge the diagnostic engine and AI
assistant use (known-issue notes, the AI persona, and whether the bundled
per-code notes — which are VAG-flavored — should be shown). The standard OBD-II
fault codes and the data-driven heuristics are universal and shared by all
profiles.

Standard-library only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

from ._dtc_data import KNOWN_ISSUES as _VAG_ISSUES

_GENERIC_PERSONA = (
    "You are an expert automotive diagnostic assistant for OBD-II vehicles of any "
    "make. Help the user diagnose their car from the data below. Be specific and "
    "practical: name the most likely causes, the checks to confirm them, and "
    "typical fixes, ordered by likelihood. If the data is insufficient, say what to "
    "log next. Keep safety in mind."
)
_VAG_PERSONA = (
    "You are an expert VAG/Audi (VW / Audi / SEAT / Škoda) diagnostic assistant. "
    "Use VAG-specific knowledge where relevant (PCV/crankcase breather, carbon "
    "build-up on direct-injection intake valves, diverter valves, HPFP cam "
    "follower, timing-chain tensioners). " + _GENERIC_PERSONA
)
_FORD_PERSONA = (
    "You are an expert Ford / Lincoln diagnostic assistant. Use Ford-specific "
    "knowledge where relevant (EcoBoost intercooler condensation, 1.5/1.6 EcoBoost "
    "coolant intrusion, PCV faults, electronic throttle-body limp mode). "
    + _GENERIC_PERSONA
)

_FORD_ISSUES: Dict[str, str] = {
    "pcv_failure": "A failed PCV/crankcase ventilation can cause lean codes "
                   "(P0171/P0174), rough idle and a vacuum-leak whistle.",
    "ecoboost_condensation": "EcoBoost intercoolers collect condensation that can "
                             "cause a stumble or misfire under boost in humid/cold conditions.",
    "coolant_intrusion": "Some 1.5/1.6 EcoBoost engines suffer coolant intrusion into a "
                         "cylinder — investigate coolant loss combined with misfires.",
    "throttle_body": "Ford electronic throttle bodies can trip limp mode "
                     "(e.g. P2111) — cleaning or replacement is often required.",
    "timing_chain": "The 3.5 EcoBoost (and other Ford engines) can stretch a timing chain or wear "
                    "a cam phaser at higher mileage — a cold-start rattle and cam/crank correlation "
                    "codes (P0016–P0019) are the tell. Confirm actual-vs-specified cam timing "
                    "(FORScan) and inspect the chain/tensioner.",
}


def _persona(make_line: str) -> str:
    return f"You are an expert {make_line} diagnostic assistant. " + _GENERIC_PERSONA


_GM_PERSONA = _persona(
    "GM (Chevrolet / GMC / Buick / Cadillac / Pontiac) — knowing AFM/DoD lifter "
    "collapse on V8s, 2.4 Ecotec oil consumption, intake-manifold-gasket leaks on "
    "3.1/3.4 V6s, and Passlock anti-theft (P1626/P1631) faults")
_TOYOTA_PERSONA = _persona(
    "Toyota / Lexus / Scion — knowing 2AZ-FE oil consumption (piston rings), VVT-i "
    "oil-line leaks, carbon build-up on direct-injection engines, and P0420 catalyst "
    "efficiency")
_HONDA_PERSONA = _persona(
    "Honda / Acura — knowing VTEC solenoid leaks, P0420 catalyst, EVAP (P1457) and "
    "engine-mount wear")
_NISSAN_PERSONA = _persona(
    "Nissan / Infiniti — knowing Jatco CVT judder/failure (P17F0, P0744), QR25 timing-"
    "chain rattle, and ignition-coil failures (P1320)")
_MAZDA_PERSONA = _persona(
    "Mazda — knowing SkyActiv direct-injection carbon build-up, VVT actuator cold-start "
    "rattle, and post-cat fuel-trim codes (P2096/P2097)")
_SUBARU_PERSONA = _persona(
    "Subaru — knowing EJ25 head-gasket leaks, FB-series oil consumption, AVCS solenoid "
    "screen clogging (P0011/P0021) and P0420 catalyst")
_HYUNDAI_PERSONA = _persona(
    "Hyundai / Kia / Genesis — knowing Theta II GDI connecting-rod-bearing failure "
    "(KSDS P1326), GDI carbon, and oil consumption")
_MOPAR_PERSONA = _persona(
    "Chrysler / Dodge / Jeep / Ram (Mopar) — knowing 3.6 Pentastar cylinder-head "
    "ticking, 5.7 Hemi MDS lifter tick, TIPM faults, and oil-pressure-sensor codes (P0521)")
_BMW_PERSONA = _persona(
    "BMW / Mini — knowing VANOS solenoids, valve-cover & oil-filter-housing gasket "
    "leaks, electric water pump/thermostat, and N54/N55 charge-pipe + HPFP issues")
_MERCEDES_PERSONA = _persona(
    "Mercedes-Benz — knowing oil/coolant leaks (valve cover, oil cooler), M272/M273 "
    "balance-shaft & intake-runner wear, 722.x transmission conductor plate, and "
    "camshaft-adjuster faults")

_GM_ISSUES = {
    "afm_lifter": "GM V8s with Active Fuel Management / Dynamic Fuel Management can collapse a "
                  "lifter, causing a misfire (often P0300/single-cylinder) and a tick.",
    "ecotec_oil": "2.4L Ecotec engines are known for high oil consumption (PCV/piston rings) — "
                  "watch for low-oil and lean conditions.",
    "passlock": "Passlock anti-theft can disable fuel (P1626/P1631) — usually needs the 30-minute "
                "security relearn, not a sensor.",
}
_TOYOTA_ISSUES = {
    "oil_consumption": "2AZ-FE engines consume oil via worn piston rings — check level often; low "
                       "oil can trigger VVT codes.",
    "vvt": "VVT-i/Dual VVT-i depends on clean oil pressure; P1349/P1656 often trace to the oil "
           "control valve or low/dirty oil.",
}
_HONDA_ISSUES = {
    "vtec": "VTEC solenoid o-rings leak and the oil-pressure switch fails, tripping P1259 — check "
            "oil level and the solenoid before the cam gear.",
    "evap": "Honda EVAP leaks (P1457) commonly trace to the canister vent shut valve.",
}
_NISSAN_ISSUES = {
    "cvt": "Jatco CVTs judder and overheat as the fluid degrades (P17F0, P0744). Service the fluid "
           "early; severe judder usually means belt/pulley or valve-body wear.",
    "coils": "Ignition coils fail commonly (P1320) — replace as a set with plugs.",
}
_MAZDA_ISSUES = {
    "skyactiv_carbon": "SkyActiv direct injection builds carbon on intake valves — rough idle and "
                       "misfires; remedy is a walnut-blast clean.",
    "vvt_rattle": "A cold-start rattle on SkyActiv often points to the VVT actuator.",
}
_SUBARU_ISSUES = {
    "head_gasket": "EJ25 engines are prone to external head-gasket leaks — check for oil/coolant "
                   "seepage and overheating.",
    "avcs": "AVCS solenoid screens clog with dirty oil, tripping P0011/P0021 — clean oil and the "
            "solenoid filter.",
}
_HYUNDAI_ISSUES = {
    "theta_rod_bearing": "Theta II 2.0T/2.4 GDI engines suffer connecting-rod-bearing failure; the "
                         "KSDS flags it as P1326. Listen for rod knock — frequently a recall item.",
    "gdi_carbon": "GDI engines build intake-valve carbon causing rough idle/misfires.",
}
_MOPAR_ISSUES = {
    "pentastar_head": "Early 3.6 Pentastar left cylinder heads tick and can misfire (often cyl 2) "
                      "from valve-seat wear.",
    "hemi_tick": "5.7 Hemi MDS lifters can tick/fail, causing a misfire.",
}
_BMW_ISSUES = {
    "gasket_leaks": "Valve-cover and oil-filter-housing gaskets leak with age — common oil smell/"
                    "burning.",
    "cooling": "Electric water pumps and plastic thermostats fail; turbo (N54/N55) charge pipes "
               "crack and the HPFP can fail.",
}
_MERCEDES_ISSUES = {
    "leaks": "Oil/coolant leaks from the valve cover and oil cooler are common.",
    "balance_shaft": "M272/M273 engines can wear the balance-shaft/idler gear, setting camshaft-"
                     "correlation codes.",
}


@dataclass
class Profile:
    id: str
    label: str
    ai_persona: str
    known_issues: Dict[str, str] = field(default_factory=dict)
    # Whether to show the bundled per-code notes (which are written for VAG).
    code_notes: bool = False


PROFILES: Dict[str, Profile] = {
    "generic": Profile("generic", "Generic OBD-II", _GENERIC_PERSONA, {}, code_notes=False),
    "vag": Profile("vag", "VAG (VW / Audi / SEAT / Škoda)", _VAG_PERSONA,
                   dict(_VAG_ISSUES), code_notes=True),
    "ford": Profile("ford", "Ford / Lincoln", _FORD_PERSONA, _FORD_ISSUES),
    "gm": Profile("gm", "GM (Chevrolet / GMC / Buick / Cadillac)", _GM_PERSONA, _GM_ISSUES),
    "toyota": Profile("toyota", "Toyota / Lexus / Scion", _TOYOTA_PERSONA, _TOYOTA_ISSUES),
    "honda": Profile("honda", "Honda / Acura", _HONDA_PERSONA, _HONDA_ISSUES),
    "nissan": Profile("nissan", "Nissan / Infiniti", _NISSAN_PERSONA, _NISSAN_ISSUES),
    "mazda": Profile("mazda", "Mazda", _MAZDA_PERSONA, _MAZDA_ISSUES),
    "subaru": Profile("subaru", "Subaru", _SUBARU_PERSONA, _SUBARU_ISSUES),
    "hyundai": Profile("hyundai", "Hyundai / Kia / Genesis", _HYUNDAI_PERSONA, _HYUNDAI_ISSUES),
    "mopar": Profile("mopar", "Chrysler / Dodge / Jeep / Ram", _MOPAR_PERSONA, _MOPAR_ISSUES),
    "bmw": Profile("bmw", "BMW / Mini", _BMW_PERSONA, _BMW_ISSUES),
    "mercedes": Profile("mercedes", "Mercedes-Benz", _MERCEDES_PERSONA, _MERCEDES_ISSUES),
}

DEFAULT_PROFILE = "vag"


def get_profile(profile) -> Profile:
    """Resolve a profile id (or Profile) to a Profile, falling back to default."""
    if isinstance(profile, Profile):
        return profile
    return PROFILES.get(profile or DEFAULT_PROFILE, PROFILES[DEFAULT_PROFILE])
