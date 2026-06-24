"""Shared pytest fixtures.

Generates the synthetic sample files once per session so every test works
against the same VCDS-style inputs without any hardware.
"""

from __future__ import annotations

import os
import sys

import pytest

# Make ``src/`` importable without an editable install.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
_SCRIPTS = os.path.join(_ROOT, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)


@pytest.fixture(scope="session")
def samples_dir(tmp_path_factory):
    import make_samples

    d = tmp_path_factory.mktemp("samples")
    classic = str(d / "classic_group.CSV")
    advanced = str(d / "advanced_uds.CSV")
    autoscan = str(d / "autoscan.TXT")
    make_samples.make_classic(classic)
    make_samples.make_advanced(advanced)
    make_samples.make_autoscan(autoscan)
    return {
        "dir": str(d),
        "classic": classic,
        "advanced": advanced,
        "autoscan": autoscan,
    }
