"""Generate synthetic VCDS-style sample files for testing and demos.

Produces three files in the target directory (default ``samples/``):

  * ``classic_group.CSV``  — old "group" layout, semicolon-delimited with
    comma decimals (a non-US Windows locale), two logged groups each with
    their own TIME column.
  * ``advanced_uds.CSV``   — newer Advanced/UDS layout, comma-delimited with
    period decimals, a Marker column, a single TIME column, a
    specified/actual pair and an intermittent misfire counter.
  * ``autoscan.TXT``       — an Auto-Scan fault report.

The two CSV layouts deliberately differ in delimiter, decimal mark and header
shape so the parser's structure inference is exercised by the test-suite.
"""

from __future__ import annotations

import argparse
import math
import os
from typing import List


def _fmt_comma(value: float, decimals: int = 1) -> str:
    """Format a number with a comma decimal mark (German/EU locale style)."""
    return f"{value:.{decimals}f}".replace(".", ",")


def make_classic(path: str, n: int = 80) -> None:
    """Classic group log: ';' delimiter, comma decimals, two TIME columns."""
    # Columns: TIME_A, Engine Speed, Intake Air Temp, TIME_B, Boost Pressure, Throttle Angle
    header_meta = ["", "Group A:", "'115", "", "Group B:", "'020"]
    header_names = [
        "TIME",
        "Engine Speed",
        "Intake Air Temp",
        "TIME",
        "Boost Pressure",
        "Throttle Angle",
    ]
    header_units = ["s", "/min", "°C", "s", "mbar", "%"]

    lines: List[str] = []
    # A couple of banner/blank rows above the header, like real VCDS files.
    lines.append("Saturday;13;June;2020;11:33:39")
    lines.append("")
    lines.append(";".join(header_meta))
    lines.append(";".join(header_names))
    lines.append(";".join(header_units))

    for i in range(n):
        t = i * 0.2  # 5 Hz
        rpm = 850 + 1500 * (math.sin(i / 9.0) * 0.5 + 0.5) + (i % 3)
        iat = 24 + i * 0.05
        boost = 1000 + 700 * max(0.0, math.sin(i / 7.0))
        throttle = 12 + 70 * max(0.0, math.sin(i / 8.0))
        row = [
            _fmt_comma(t, 1),
            _fmt_comma(rpm, 1),
            _fmt_comma(iat, 1),
            _fmt_comma(t, 1),
            _fmt_comma(boost, 1),
            _fmt_comma(throttle, 1),
        ]
        lines.append(";".join(row))

    with open(path, "w", encoding="utf-8-sig", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def make_advanced(path: str, n: int = 120) -> None:
    """Advanced/UDS log: ',' delimiter, period decimals, single TIME column."""
    # Columns: Marker, TIME, Engine Speed, Boost Pressure (specified),
    #          Boost Pressure (actual), Coolant Temp, Misfire Counter Cyl 1
    header_names = [
        "Marker",
        "TIME",
        "Engine Speed",
        "Boost Pressure (specified)",
        "Boost Pressure (actual)",
        "Coolant Temp",
        "Misfire Counter Cyl 1",
    ]
    header_units = ["", "s", "/min", "mbar", "mbar", "°C", "count"]

    lines: List[str] = []
    lines.append(",".join(header_names))
    lines.append(",".join(header_units))

    misfire = 0
    for i in range(n):
        t = i * 0.2
        rpm = 800 + 2200 * (math.sin(i / 11.0) * 0.5 + 0.5)
        spec = 1200 + 900 * max(0.0, math.sin(i / 10.0))
        # actual tracks spec but drops out badly in a mid-log window
        if 50 <= i <= 70:
            actual = spec - 600  # boost shortfall — the interesting event
        else:
            actual = spec - 40 * math.sin(i / 4.0)
        coolant = 60 + 30 * (1 - math.exp(-i / 40.0))
        # intermittent misfire: counter climbs only during the shortfall window
        if 52 <= i <= 68 and i % 2 == 0:
            misfire += 1
        marker = "X" if i == 55 else ""
        row = [
            marker,
            f"{t:.1f}",
            f"{rpm:.1f}",
            f"{spec:.1f}",
            f"{actual:.1f}",
            f"{coolant:.1f}",
            str(misfire),
        ]
        lines.append(",".join(row))

    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


AUTOSCAN_TEXT = """\
VCDS -- Windows Based VAG/VAS Emulator
VCDS Version: 21.3.0 (x64)
Data version: 20210525 DS296.0

Saturday,13,June,2020,11:40:02:00000

VIN: WAUZZZ8K9BA123456   Mileage: 123456km-198400mi

--------------------------------------------------------------------------------

Address 01: Engine (J623-CDNB)       Labels: 06J-907-115.clb
   Part No SW: 8K0 907 115 P    HW: 06J 907 309 K
   Component: 3.0l V6 TFSI   H08 0001

   2 Faults Found:
   008598 - Boost Pressure Regulation
            P2196 - 000 - Signal too High - Intermittent
   000257 - Cylinder 1 Misfire Detected
            P0301 - 000 - Upper Limit Exceeded - Intermittent

Address 03: ABS Brakes (J104)        Labels: 8K0-614-517.clb
   Part No SW: 8K0 614 517 AH   HW: 8K0 907 379 AB
   Component: ESP9320 H40 0210

   No fault code found.

Address 17: Instruments (J285)       Labels: 8K0-920-900.clb
   Part No SW: 8K0 920 983 R    HW: 8K0 920 983 R

   1 Fault Found:
   01314 - Engine Control Module
            B1681 - 008 - No Communications - Intermittent

End-------------------------(Elapsed Time: 01:12)--------------------------
"""


def make_autoscan(path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(AUTOSCAN_TEXT)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic VCDS sample files.")
    parser.add_argument(
        "outdir",
        nargs="?",
        default="samples",
        help="Directory to write sample files into (default: ./samples).",
    )
    args = parser.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    classic = os.path.join(args.outdir, "classic_group.CSV")
    advanced = os.path.join(args.outdir, "advanced_uds.CSV")
    autoscan = os.path.join(args.outdir, "autoscan.TXT")

    make_classic(classic)
    make_advanced(advanced)
    make_autoscan(autoscan)

    print(f"Wrote:\n  {classic}\n  {advanced}\n  {autoscan}")


if __name__ == "__main__":
    main()
