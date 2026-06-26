# Third-party notices

OBD Toolkit bundles or depends on the following components. Each remains under
its own license; this file is an attribution summary, not a re-license.

| Component | Purpose | License |
|-----------|---------|---------|
| **PySide6 / Qt for Python** | Desktop GUI | **LGPL-3.0** (Qt under LGPLv3) |
| **pyqtgraph** | Plotting | MIT |
| **python-OBD** (`obd`) | ELM327 / OBD-II access | **GPL-2.0-only** ⚠️ |
| **pyserial** | Serial port I/O | BSD-3-Clause |
| **pint** | Unit handling | BSD-3-Clause |
| **certifi** | CA bundle for HTTPS (updater) | MPL-2.0 |
| **mcp** (Model Context Protocol SDK) | MCP server | MIT |
| **lucide** icons | Sidebar / button icons (SVG paths) | ISC |
| Inno Setup | Windows installer build tool (not bundled at runtime) | Inno Setup License |

## ⚠️ Important: python-OBD is GPL-2.0

`python-OBD` is licensed **GPL-2.0-only**. It is bundled into the distributed
Windows/macOS binaries. GPL is copyleft, and its obligations trigger **on
distribution** (personal/internal use is unaffected). Before a public production
release, choose one of:

1. **License the whole project under a GPL-2.0-compatible license** and make the
   source available — simplest if you are happy to open-source it.
2. **Drop python-OBD** and use the app's built-in raw ELM327 driver
   (`RawELM327Connection`) for the live path, removing the GPL dependency. This
   frees you to choose any license (incl. proprietary).
3. Keep it **personal/internal only** (no public distribution).

A permissive license (e.g. MIT) for the project would be **incompatible** with
distributing GPL python-OBD as-is — option 1 or 2 is required for that.

## LGPL (PySide6/Qt) note

Qt is used under LGPL-3.0. Distribute the means to relink against a modified Qt
(PyInstaller ships the Qt shared libraries, which satisfies this in practice),
and keep this attribution available to users.
