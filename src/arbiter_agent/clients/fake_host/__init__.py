"""Scriptable fake client (M1.3): emits Codex/Claude-shaped hooks over each transport and writes
transcripts, so every milestone can be tested without real clients."""

from arbiter_agent.clients.fake_host.host import FakeHost, HookCall

__all__ = ["FakeHost", "HookCall"]
