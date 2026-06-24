# Example files

Synthetic VCDS-style files committed for demos, manual testing and as a quick
target for the tools without a car. Regenerate them any time with:

```powershell
python scripts/make_samples.py examples
```

| File | What it exercises |
|------|-------------------|
| `classic_group.CSV` | Old **classic group** layout — `;`-delimited with **comma decimals** (non-US Windows locale), two logged groups each with their own `TIME` column, group metadata (`Group A:`, `'115`) that must be stripped from channel names. |
| `advanced_uds.CSV` | Newer **Advanced/UDS** layout — `,`-delimited with period decimals, a `Marker` column, a single `TIME` column, a specified-vs-actual boost pair and an intermittent misfire counter (drives the divergence + rising-counter event heuristics). |
| `autoscan.TXT` | An **Auto-Scan** fault report — VIN/mileage header, three modules (one with two faults, one clean, one with one fault) where each fault's indented `P####` line is a **status detail**, not a new fault. |

## Try them

```powershell
# Parse + report on all of them:
python scripts/smoke_logs.py examples

# Or point the toolkit at this folder:
$env:VCDS_LOGS_DIR = (Resolve-Path examples)
vcds-gui            # File Analyzer → Open these files
```

> These are **not** real vehicle captures — values are generated. They exist so
> the parser, event detection and GUI can be exercised with zero hardware.
