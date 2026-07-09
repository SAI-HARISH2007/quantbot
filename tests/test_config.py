from pathlib import Path

from quantbot.config import AppConfig, load_config


def test_defaults_are_sane():
    cfg = AppConfig()
    assert cfg.risk.kelly_fraction <= 0.5  # never full Kelly by default
    assert cfg.risk.max_drawdown_pct < 1.0
    assert cfg.polymarket.clob_url.startswith("https://")


def test_yaml_roundtrip(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "risk:\n  initial_capital: 777.0\nstrategies:\n"
        "  - name: momentum\n    enabled: true\n    params:\n      lookback: 99\n"
    )
    cfg = load_config(p)
    assert cfg.risk.initial_capital == 777.0
    assert cfg.strategies[0].name == "momentum"
    assert cfg.strategies[0].params["lookback"] == 99


def test_env_override(monkeypatch):
    monkeypatch.setenv("QUANTBOT_RISK__KELLY_FRACTION", "0.1")
    cfg = AppConfig()
    assert cfg.risk.kelly_fraction == 0.1


def test_missing_file_falls_back_to_defaults(tmp_path: Path):
    cfg = load_config(tmp_path / "nope.yaml")
    assert cfg.risk.initial_capital == 10_000.0
