"""Backward-compatible import surface for the generic channel runtime bootstrap.

The implementation moved to :mod:`app.services.channel_runtime_bootstrap` so
production bootstrap no longer carries a channel-name/niche hardcode. Existing
imports continue to work without making the historical first channel runtime
truth.
"""

from app.services.channel_runtime_bootstrap import (
    Phase1RuntimeAuthority,
    Phase1RuntimeBootstrapResult,
    Phase1RuntimeBootstrapService,
)

__all__ = [
    "Phase1RuntimeAuthority",
    "Phase1RuntimeBootstrapResult",
    "Phase1RuntimeBootstrapService",
]
