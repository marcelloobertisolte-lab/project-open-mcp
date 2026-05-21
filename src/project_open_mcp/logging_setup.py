"""Centralised logging configuration.

Logs go to stderr and to a rotating file. **Never to stdout**: under the stdio
transport, stdout carries the MCP JSON-RPC stream and must stay clean.

Environment:
    PO_LOG_LEVEL  Logging level (default INFO). e.g. DEBUG, INFO, WARNING.
    PO_LOG_FILE   Log file path (default: ``logs/project-open-mcp.log`` under
                  the current working directory). Set to empty to disable the
                  file handler and log only to stderr.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging() -> logging.Logger:
    """Configure root logging once. Idempotent."""
    global _CONFIGURED
    logger = logging.getLogger("project_open_mcp")
    if _CONFIGURED:
        return logger

    level_name = os.environ.get("PO_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(_FORMAT)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    root.addHandler(stderr_handler)

    log_file = os.environ.get("PO_LOG_FILE", "logs/project-open-mcp.log")
    if log_file:
        # A bad/unwritable log path must never crash the server: fall back to
        # stderr-only logging and warn instead.
        try:
            path = Path(log_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                path, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError as exc:
            log_file = f"<file disabled: {exc}>"
            root.warning("Could not open log file (%s); logging to stderr only", exc)

    # httpcore is extremely chatty; keep it quiet unless we are debugging.
    logging.getLogger("httpcore").setLevel(
        logging.DEBUG if level <= logging.DEBUG else logging.WARNING
    )

    _CONFIGURED = True
    logger.info(
        "Logging configured: level=%s file=%s", level_name, log_file or "<stderr only>"
    )
    return logger
