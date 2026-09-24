"""Arbiter: a local control plane for AI coding agents.

Keep this module import-light: the hook CLI and MCP shim import the package on every
invocation, so nothing heavy (yaml, sqlite) may be imported here.
"""

__version__ = "0.1.0"

# Shim <-> daemon IPC protocol (spec §16.5). Bump MINOR for compatible additions,
# MAJOR for breaking changes.
PROTOCOL_MAJOR = 1
PROTOCOL_MINOR = 0
