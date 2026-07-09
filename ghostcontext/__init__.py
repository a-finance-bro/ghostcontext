"""GhostContext — agent-friendly, event-driven session recording for macOS.

Silently observes screen state, IDE activity, and a live mic+system-audio
transcript, and emits a token-optimized XML timeline that text-based coding
agents (Claude, etc.) can ingest to understand exactly what you were doing and
referring to during a live/pairing session.
"""

__version__ = "0.3.0"
