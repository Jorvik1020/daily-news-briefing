import yaml
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _load(name):
    return yaml.safe_load((ROOT / "config" / name).read_text())


def test_example_sources_parses():
    cfg = _load("sources.example.yaml")
    assert isinstance(cfg["domains"], dict) and cfg["domains"]
    assert "ft.com" in cfg["domains"]
    assert len(cfg["topics"]) == 4
    # forwarders + subject markers for the forwarded-newsletter path
    assert cfg["forwarders"] and all("@" in f for f in cfg["forwarders"])
    assert cfg["subject_markers"]["in today's ft"] == "FT"
    assert cfg["default_forward_label"]
    # web fallback config (housing + smart-money)
    wf = cfg["web_fallback"]
    assert wf["housing"] and wf["smart_money"]
    assert any("rightmove" in q.lower() for q in wf["housing"])
    # x_voices ships empty (opt-in)
    assert wf["x_voices"] == []


def test_example_sources_uses_placeholder_forwarders():
    # The shipped example must use placeholder mailboxes, not real addresses.
    cfg = _load("sources.example.yaml")
    for fwd in cfg["forwarders"]:
        assert fwd.endswith("@example.com"), f"non-placeholder forwarder: {fwd}"


def test_gateway_config_parses():
    cfg = yaml.safe_load((ROOT / "gateway" / "config.yaml").read_text())
    assert cfg["default"] == "claude"
    assert cfg["aliases"]["claude"] == "anthropic/claude-opus-4-8"
    assert cfg["fallback"]["claude"] == "claude-fast"
