"""Entry point for the Snipe-IT MCP server.

Ordering matters here — modelled on the Zammad-MCP entry point:

1. :func:`configure_logging` runs *first* so any subsequent module-import-time
   log line is routed to stderr. The stdio MCP transport reserves stdout for
   the JSON-RPC stream; a stray log on stdout breaks the protocol.
2. Load and validate transport + Snipe-IT auth config from the environment so
   misconfigurations fail fast with helpful messages before any FastMCP code
   runs.
3. Lazy-import ``mcp`` from :mod:`snipeit_mcp.mcp_server` — this is when the
   FastMCP instance is built and tool modules register their decorators.
4. Run the server on the chosen transport.
"""

import sys

from .config import (
    AuthMode,
    ConfigError,
    SnipeITAuthConfig,
    TransportConfig,
    TransportType,
)
from .logging_config import configure_logging


def main() -> None:
    """Run the Snipe-IT MCP server with environment-driven configuration."""
    configure_logging()

    try:
        transport = TransportConfig.from_env()
        transport.validate()
        auth = SnipeITAuthConfig.from_env()
        auth.validate_with_transport(transport)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(2)

    # OAuth implies HTTP transport — validate_with_transport above already
    # rejected the OAuth+stdio combination, so by this point the transport is
    # consistent with the auth mode.

    from .mcp_server import mcp  # noqa: PLC0415 — lazy import after logging+config

    if transport.transport == TransportType.HTTP:
        mode_label = "OAuth" if auth.mode == AuthMode.OAUTH else "API-key"
        print(
            f"Starting Snipe-IT MCP Server (HTTP, {mode_label} mode) "
            f"on http://{transport.host}:{transport.port}",
            file=sys.stderr,
        )
        mcp.run(transport="http", host=transport.host, port=transport.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
