"""Typed configuration: packaged defaults (spec §26) + validated user overrides."""

from arbiter_agent.config.loader import Config, ConfigError, load_config, load_defaults

__all__ = ["Config", "ConfigError", "load_config", "load_defaults"]
