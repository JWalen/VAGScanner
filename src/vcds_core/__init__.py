"""vcds_core — dependency-free parsing/analysis core for VCDS & OBD data.

Every front-end (MCP server, GUI, live logger) relies on this package, so it
MUST remain standard-library only.
"""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("vcds-toolkit")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+source"

from .parse import (
    AutoScan,
    Channel,
    Event,
    Fault,
    MeasuringLog,
    Module,
    find_events,
    parse_autoscan,
    parse_measuring_log,
)
from . import compute, knowledge
from .compute import add_computed_channels
from .diagnose import DiagnosticReport, Finding, diagnose, report_to_text
from .report import build_html_report, save_html_report

__all__ = [
    "__version__",
    "add_computed_channels",
    "build_html_report",
    "compute",
    "diagnose",
    "DiagnosticReport",
    "Finding",
    "knowledge",
    "save_html_report",
    "AutoScan",
    "Channel",
    "Event",
    "Fault",
    "MeasuringLog",
    "Module",
    "find_events",
    "parse_autoscan",
    "parse_measuring_log",
]
