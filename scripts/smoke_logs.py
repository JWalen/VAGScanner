"""Smoke-test the parser against a real VCDS Logs folder.

Points at ``VCDS_LOGS_DIR`` (default ``C:\\Ross-Tech\\VCDS\\Logs``). When that
folder does not exist (e.g. on a dev box with no VCDS install), it generates the
synthetic samples into a temp folder and runs against those instead, so the
smoke test is always runnable.

Usage:
    python scripts/smoke_logs.py [LOGS_DIR]

Exits non-zero if any file in the folder fails to parse.
"""

from __future__ import annotations

import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
for p in (_SRC, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from vcds_core import parse  # noqa: E402


def _resolve_dir(argv) -> str:
    if len(argv) > 1 and argv[1]:
        return argv[1]
    env = os.environ.get("VCDS_LOGS_DIR")
    if env and os.path.isdir(env):
        return env
    default = r"C:\Ross-Tech\VCDS\Logs"
    if os.path.isdir(default):
        return default
    # Prefer the committed examples folder if it exists.
    examples = os.path.join(os.path.dirname(_HERE), "examples")
    if os.path.isdir(examples) and any(
        f.lower().endswith((".csv", ".txt")) for f in os.listdir(examples)
    ):
        print(f"[smoke] No VCDS Logs folder found; using committed examples in {examples}")
        return examples
    # Otherwise generate throwaway samples.
    import make_samples

    tmp = tempfile.mkdtemp(prefix="vcds_smoke_")
    make_samples.make_classic(os.path.join(tmp, "classic_group.CSV"))
    make_samples.make_advanced(os.path.join(tmp, "advanced_uds.CSV"))
    make_samples.make_autoscan(os.path.join(tmp, "autoscan.TXT"))
    print(f"[smoke] No VCDS Logs folder found; using generated samples in {tmp}")
    return tmp


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv
    logs_dir = _resolve_dir(argv)
    print(f"[smoke] Scanning {logs_dir}")
    if not os.path.isdir(logs_dir):
        print(f"[smoke] ERROR: not a directory: {logs_dir}")
        return 2

    files = [
        f for f in sorted(os.listdir(logs_dir))
        if f.lower().endswith((".csv", ".txt")) and os.path.isfile(os.path.join(logs_dir, f))
    ]
    if not files:
        print("[smoke] No .CSV/.TXT files found.")
        return 0

    failures = 0
    for name in files:
        path = os.path.join(logs_dir, name)
        kind = parse.classify_file(path)
        try:
            if kind == "autoscan":
                scan = parse.parse_autoscan(path)
                print(
                    f"[ok]  {name:32s} autoscan  VIN={scan.vin or '?'} "
                    f"modules={len(scan.modules)} faults={scan.total_faults}"
                )
            else:
                mlog = parse.parse_measuring_log(path)
                ev = parse.find_events(mlog)
                print(
                    f"[ok]  {name:32s} log  fmt={mlog.format_guess} "
                    f"delim={mlog.delimiter} ch={len(mlog.channels)} "
                    f"samples={mlog.sample_count} events={len(ev)}"
                )
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"[FAIL] {name:32s} {type(exc).__name__}: {exc}")

    print(f"[smoke] {len(files) - failures}/{len(files)} parsed cleanly.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
