"""``python -m reacher.mcp`` / the ``reacher-mcp`` console script.

The SDK check lives here rather than in ``server.py`` so that a user who ran a
plain ``pip install reacher2p`` gets a sentence telling them what to install,
instead of a ``ModuleNotFoundError`` traceback. The console script is generated
either way, so this is the first thing many people will see.
"""

from __future__ import annotations

import sys

_MISSING_SDK = """\
reacher-mcp needs the MCP SDK, which is not part of the base install.

    pip install "reacher2p[mcp]"

Then register the server with your agent. For Claude Code, in .mcp.json:

    {"mcpServers": {"reacher": {"command": "reacher-mcp"}}}

See docs/mcp-server.md for the full setup.
"""


def main() -> int:
    try:
        import mcp  # noqa: F401
    except ModuleNotFoundError:
        print(_MISSING_SDK, file=sys.stderr)
        return 1

    from .server import main as serve

    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
