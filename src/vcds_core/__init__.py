"""vcds_core — dependency-free parsing/analysis core for VCDS & OBD data.

Every front-end (MCP server, GUI, live logger) relies on this package, so it
MUST remain standard-library only.
"""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("vcds-toolkit")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.1.0"

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

__all__ = [
    "__version__",
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
