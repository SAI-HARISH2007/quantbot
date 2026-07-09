from datetime import datetime, timezone

import pytest

from quantbot.fairvalue.base import FairValueContext
from quantbot.fairvalue.digital_option import (
    DigitalOptionModel,
    digital_call_prob,
    parse_threshold_market,
)
from quantbot.fairvalue.ensemble import EnsembleFairValue
from quantbot.fairvalue.market_models import MicropriceModel, TimeWeightedModel
from quantbot.fairvalue.vol import ESTIMATORS, estimate_vol

from tests.conftest import make_candles, make_price_history


def test_parse_threshold_market(market):
    parsed = parse_threshold_market(market)
    assert parsed == {"symbol": "BTCUSDT", "strike": 65_000.0, "direction": "above"}


def test_parse_k_suffix(market):
    market.question = "Will ETH dip below $2.5k this week?"
    parsed = parse_threshold_market(market)
    assert parsed["symbol"] == "ETHUSDT"
    assert parsed["strike"] == 2500.0
    assert parsed["direction"] == "below"


def test_parse_rejects_non_threshold(market):
    market.question = "Will the Lakers win the 2026 NBA title?"
    assert parse_threshold_market(market) is None


def test_digital_call_prob_properties():
    # ATM short-dated ~ 0.5; deep ITM -> 1; deep OTM -> 0
    atm = digital_call_prob(100, 100, 0.5, 0.01)
    assert 0.4 < atm < 0.55
    assert digital_call_prob(200, 100, 0.5, 0.05) > 0.95
    assert digital_call_prob(50, 100, 0.5, 0.05) < 0.05
    # expiry boundary
    assert digital_call_prob(101, 100, 0.5, 0.0) == 1.0
    assert digital_call_prob(99, 100, 0.5, 0.0) == 0.0


def test_digital_prob_monotone_in_spot():
    probs = [digital_call_prob(s, 100, 0.6, 0.1) for s in (80, 90, 100, 110, 120)]
    assert probs == sorted(probs)


def test_digital_option_model_end_to_end(market):
    model = DigitalOptionModel()
    ctx = FairValueContext(
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
        market=market,
        spot=66_000.0,
        candles=make_candles(),
    )
    est = model.estimate(ctx)
    assert est is not None
    assert 0.0 < est.prob < 1.0
    assert est.prob > 0.5  # spot above strike -> more likely YES
    assert est.detail["strike"] == 65_000.0


def test_digital_option_selects_candles_by_symbol(market):
    """A BTC market must never be priced off ETH candles."""
    btc = make_candles()  # ~60k
    eth = make_candles(start=3_000.0, seed=9)
    ctx = FairValueContext(
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
        market=market,  # question parses to BTCUSDT, strike 65k
        candles_by_symbol={"ETHUSDT": eth, "BTCUSDT": btc},
    )
    est = DigitalOptionModel().estimate(ctx)
    assert est is not None
    assert abs(est.detail["spot"] - float(btc["close"].iloc[-1])) < 1e-9

    # symbol missing entirely -> no estimate rather than wrong estimate
    ctx2 = FairValueContext(
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
        market=market,
        candles_by_symbol={"ETHUSDT": eth},
    )
    assert DigitalOptionModel().estimate(ctx2) is None


def test_vol_estimators_agree_on_order_of_magnitude():
    candles = make_candles(n=2000, vol=0.0005)
    vols = {name: estimate_vol(candles, name) for name in ESTIMATORS}
    for v in vols.values():
        assert 0.05 < v < 2.0, vols
    # per-minute sigma=5e-4 -> annualized ~ 0.36; c2c should be close
    assert abs(vols["close_to_close"] - 0.36) < 0.15


def test_microprice_model(market, book):
    ctx = FairValueContext(
        now=datetime.now(timezone.utc), market=market, book=book
    )
    est = MicropriceModel().estimate(ctx)
    assert est is not None
    assert abs(est.prob - book.microprice()) < 1e-9


def test_ensemble_combines_and_widens_on_disagreement(market, book):
    ctx = FairValueContext(
        now=datetime.now(timezone.utc),
        market=market,
        book=book,
        pm_price_history=make_price_history(),
    )
    ens = EnsembleFairValue([MicropriceModel(), TimeWeightedModel()])
    est = ens.estimate(ctx)
    assert est is not None
    parts = est.detail
    assert set(parts) == {"microprice", "ewma_price"}
    lo = min(p["prob"] for p in parts.values())
    hi = max(p["prob"] for p in parts.values())
    assert lo - 1e-9 <= est.prob <= hi + 1e-9


def test_ensemble_requires_min_models(market):
    ctx = FairValueContext(now=datetime.now(timezone.utc), market=market)
    ens = EnsembleFairValue([MicropriceModel()], min_models=1)
    assert ens.estimate(ctx) is None  # no book -> no estimate


@pytest.mark.parametrize("bad_q", ["", "Random question about sports $"])
def test_digital_model_returns_none_when_unpriceable(market, bad_q):
    market.question = bad_q
    assert DigitalOptionModel().estimate(
        FairValueContext(now=datetime.now(timezone.utc), market=market)
    ) is None
