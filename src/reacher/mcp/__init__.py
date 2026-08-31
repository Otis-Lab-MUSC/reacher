"""Cross-repository change tooling for REACHER, exposed over MCP.

The server helps a user's own coding agent make coordinated changes across the
three layers of the platform — firmware, kernel, frontend — by supplying ground
truth about the registries, an ordered checklist per change kind, and
verification that catches mirrors which have fallen out of sync.

Two rules shape everything here:

1. **This package holds zero copies of any registry.** It parses live source at
   call time. A server that cached the tables would become one more mirror to
   drift, which is the exact problem it exists to solve.
2. **Ground truth comes from the user's working tree**, never from an installed
   ``reacher2p`` wheel — see ``reacher.schema`` for why that distinction is
   load-bearing.

Nothing in this package is imported by ``reacher.api.app``, so it stays out of
the frozen application bundle.
"""

__all__ = ["__version__"]

from .. import __version__
