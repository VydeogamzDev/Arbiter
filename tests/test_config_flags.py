import pytest
import yaml

from arbiter_agent.config import ConfigError, load_config, load_defaults
from arbiter_agent.config.loader import build_config
from arbiter_agent.flags import REGISTRY, FeatureFlags


def test_defaults_match_spec_sections():
    d = load_defaults()
    for section in ("daemon", "clients", "hooks", "setup", "ipc", "privacy", "storage", "diagnostics", "completion"):
        assert section in d
    assert d["completion"]["gate_mode"] == "annotate"
    assert d["hooks"]["gating_p95_budget_ms"] == 300
    assert d["storage"]["max_payload_kb"] == 512


def test_user_override_merges(home):
    override = {"completion": {"gate_mode": "block"}, "storage": {"storage_cap_gb": 1}}
    home.config_file.write_text(yaml.safe_dump(override))
    cfg = load_config(home)
    assert cfg.get("completion.gate_mode") == "block"
    assert cfg.get("storage.storage_cap_gb") == 1
    assert cfg.get("completion.unknown_never_counts_as_pass") is True  # untouched default kept


@pytest.mark.parametrize("override, message", [
    ({"completion": {"nope": 1}}, "unknown key 'completion.nope'"),
    ({"completion": {"gate_mode": 3}}, "'completion.gate_mode' must be str"),
    ({"completion": {"gate_mode": "yolo"}}, "must be one of"),
    ({"storage": {"storage_cap_gb": 0}}, "must be > 0"),
    ({"hooks": {"fail_open": "yes"}}, "must be bool"),
    ({"features": {"not_a_flag": True}}, "unknown feature flag"),
    ({"storage": "big"}, "must be a mapping"),
])
def test_invalid_overrides_rejected(override, message):
    with pytest.raises(ConfigError, match=message.replace("(", r"\(").replace(")", r"\)")):
        build_config(override)


def test_int_accepted_for_float():
    cfg = build_config({"storage": {"storage_cap_gb": 3}})
    assert cfg.get("storage.storage_cap_gb") == 3


def test_invariant_keys_are_not_configurable():
    with pytest.raises(ConfigError, match=r"unknown key 'setup\.modify_client_permissions'"):
        build_config({"setup": {"modify_client_permissions": True}})


def test_flags_defaults_overrides_and_force_off():
    f = FeatureFlags({"completion_gate": True})
    assert f.enabled("event_log") is True
    assert f.enabled("completion_gate") is True
    assert f.enabled("speculation") is False
    f.force_off("event_log", "breaker tripped")
    assert f.enabled("event_log") is False
    assert f.snapshot()["event_log"]["forced_off"] == "breaker tripped"
    f.clear_force("event_log")
    assert f.enabled("event_log") is True
    with pytest.raises(KeyError):
        FeatureFlags({"bogus": True})
    assert all(fl.milestone for fl in REGISTRY.values())
