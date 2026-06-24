"""vcds_core — dependency-free parsing/analysis core for VCDS & OBD data.

Every front-end (MCP server, GUI, live logger) relies on this package, so it
MUST remain standard-library only.
"""

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
