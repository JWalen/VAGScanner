# Windows installer

Builds a self-contained Windows installer for the **VCDS Toolkit desktop GUI**
(`vcds-gui`). End users do **not** need Python installed — the bundle ships its
own interpreter and all of PySide6 / pyqtgraph.

> The installer covers the **GUI** only. The **MCP server** (`vcds-mcp`) is a
> developer integration for Claude Desktop / Code and is installed via
> `pip install -e ".[mcp]"` (see the top-level README), not this installer.

## What you get

- `dist\VCDS Toolkit\` — a portable, self-contained app folder (PyInstaller
  one-folder build). `VCDS Toolkit.exe` runs the GUI directly.
- `installer\Output\VCDS-Toolkit-Setup-<version>.exe` — a proper installer with
  Start-Menu (and optional desktop) shortcuts and an Add/Remove Programs entry.
  Installs per-user by default, so **no admin rights are required**.

## Prerequisites

- Python 3.10+ (your project venv is fine).
- [Inno Setup 6](https://jrsoftware.org/isdl.php) for the `Setup.exe` step.
  Without it you still get the portable folder.

## Build

From the repository root, with the venv active:

```powershell
.\.venv\Scripts\Activate.ps1
.\installer\build_installer.ps1
```

The script reads the version from `pyproject.toml`, runs PyInstaller, then
compiles the Inno Setup installer if `iscc.exe` is found.

### Manual steps (equivalent)

```powershell
pip install pyinstaller
pip install -e ".[gui]"
pyinstaller installer\vcds_gui.spec --clean --noconfirm
iscc installer\vcds-toolkit.iss /DMyAppVersion=0.1.0
```

## Notes

- **One-folder, not one-file** — chosen on purpose: faster startup and far less
  antivirus friction than the one-file temp-extraction approach on locked-down
  corporate machines.
- **Branding** — drop an `app.ico` next to the spec and set `icon=...` in
  `vcds_gui.spec` plus `SetupIconFile` in `vcds-toolkit.iss`.
- **Releases** — the GitHub Actions release workflow builds this installer and
  attaches `VCDS-Toolkit-Setup-<version>.exe` to each tagged release
  automatically (requires Inno Setup on the runner, installed via Chocolatey).
## Code signing

The release workflow signs the installer **automatically when a cert is
configured** — otherwise it ships unsigned (today's behaviour). To turn signing
on, add two repository secrets (Settings → Secrets and variables → Actions):

- `SIGNING_PFX_BASE64` — your code-signing `.pfx`, base64-encoded
- `SIGNING_PASSWORD` — the `.pfx` password

Encode the `.pfx`:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("mycert.pfx")) | Set-Clipboard
```

### Free, internal option — self-signed + deploy to your fleet
Best for an internal tool on managed (Intune/GPO) machines. Removes the
SmartScreen prompt on machines that trust your cert.

```powershell
# 1. create the cert
$c = New-SelfSignedCertificate -Type CodeSigningCert -Subject "CN=DeltaModTech" `
     -CertStoreLocation Cert:\CurrentUser\My -KeyExportPolicy Exportable `
     -KeyUsage DigitalSignature -FriendlyName "DeltaModTech Code Signing"
# 2. export the private .pfx (for signing) and the public .cer (for trust)
Export-PfxCertificate -Cert $c -FilePath deltamodtech.pfx `
     -Password (ConvertTo-SecureString "CHANGEME" -AsPlainText -Force)
Export-Certificate -Cert $c -FilePath deltamodtech.cer
```

Then base64 the `.pfx` into `SIGNING_PFX_BASE64`, set `SIGNING_PASSWORD`, and push
`deltamodtech.cer` to **Trusted Publishers** (and Trusted Root) on staff machines
via Intune/Group Policy. The next tagged release is signed; managed machines stop
prompting. (Un-managed PCs still warn — expected for a self-signed cert.)

### Public trust
For an installer trusted on **any** Windows PC, use a publicly-trusted cert.
Note: since June 2023 these must be on FIPS hardware or a cloud signing service —
you can't get a plain `.pfx` from a CA anymore. Good options:

- **Azure Trusted Signing** (~$10/mo, cloud, great CI support, good SmartScreen
  reputation). Swap the signing step for `azure/trusted-signing-action` and add
  the Azure secrets instead of the `.pfx` ones.
- **EV cert** (SSL.com / DigiCert / Certum cloud signing) — instant SmartScreen
  reputation, ~$200–600/yr.
