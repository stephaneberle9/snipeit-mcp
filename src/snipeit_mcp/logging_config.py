"""Logging configuration helpers for the Snipe-IT MCP server.

The stdio MCP transport uses stdout for JSON-RPC traffic, so any log line that
lands on stdout corrupts the protocol stream. ``configure_logging`` walks every
handler on the root logger and redirects stdout-bound ``StreamHandler`` instances
to stderr, then installs a stderr handler if none exist. Must run *before* any
other module's import-time logging.
"""

import logging
import os
import sys


def configure_logging() -> None:
    """Configure root logging so MCP stdio JSON-RPC traffic stays uncorrupted."""
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

    if log_level_str not in valid_levels:
        invalid_level = log_level_str
        log_level_str = "INFO"
        logging.getLogger(__name__).warning(
            "Invalid LOG_LEVEL '%s', defaulting to INFO. Valid values: %s",
            invalid_level,
            ", ".join(sorted(valid_levels)),
        )

    log_level = getattr(logging, log_level_str)
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Redirect any existing stdout-bound StreamHandler to stderr.
    for handler in root_logger.handlers:
        if isinstance(handler, logging.StreamHandler):
            stream = getattr(handler, "stream", None)
            if stream in {sys.stdout, sys.__stdout__}:
                handler.setStream(sys.stderr)

    # Install a stderr handler if the root logger has none yet.
    if not root_logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        root_logger.addHandler(handler)
