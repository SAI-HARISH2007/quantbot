from datetime import datetime, timezone

from quantbot.core.types import BookLevel, OrderBook, Side
from quantbot.fairvalue.base import FairValueEstimate
from quantbot.strategies.base import MarketView
from quantbot.strategies.complement_arb import ComplementArbitrage
from quantbot.strategies.fair_value_deviation import FairValueDeviation
from quantbot.strategies.mean_reversion import MeanReversion
from quantbot.strategies.momentum import Momentum
from quantbot.strategies.obi import OrderBookImbalance
from quantbot.strategies.registry import REGISTRY, build_strategies
from quantbot.config import StrategyConfig

from tests.conftest import make_price_history

NOW = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _view(market, **kw) -> MarketView:
    return MarketView(now=NOW, market=market, **kw)


def test_registry_contains_all():
    assert set(REGISTRY) == {
        "fair_value_deviation", "mean_reversion", "momentum", "obi", "complement_arb"
    }
    strats = build_strategies(
        [StrategyConfig(name="mean_reversion", params={"entry_z": 3.0})]
    )
    assert len(strats) == 1 and strats[0].entry_z == 3.0


def test_fair_value_deviation_triggers_on_gap(market):
    s = FairValueDeviation(entry_threshold=0.05)
    fv = FairValueEstimate(model="m", prob=0.70, std=0.02)
    sigs = s.on_view(_view(market, price=0.55, fair_value=fv))
    assert len(sigs) == 1
    assert sigs[0].side == Side.BUY
    assert sigs[0].edge > 0.1

    # small gap -> no signal
    fv2 = FairValueEstimate(model="m", prob=0.57, std=0.02)
    assert s.on_view(_view(market, price=0.55, fair_value=fv2)) == []

    # gap within model uncertainty -> no signal
    fv3 = FairValueEstimate(model="m", prob=0.70, std=0.30)
    assert s.on_view(_view(market, price=0.55, fair_value=fv3)) == []


def test_mean_reversion_fades_spikes(market):
    s = MeanReversion(entry_z=2.0)
    hist = make_price_history(n=100, noise=0.005)
    hist.loc[len(hist) - 1, "price"] = float(hist["price"].iloc[-2]) + 0.15  # spike up
    sigs = s.on_view(_view(market, price=float(hist["price"].iloc[-1]), history=hist))
    assert len(sigs) == 1 and sigs[0].side == Side.SELL


def test_momentum_follows_trend(market):
    s = Momentum(lookback=30, entry_move=0.04, vol_scale=1.0)
    hist = make_price_history(n=100, drift=0.004, noise=0.001)
    sigs = s.on_view(_view(market, price=float(hist["price"].iloc[-1]), history=hist))
    assert len(sigs) == 1 and sigs[0].side == Side.BUY


def test_obi_signals_on_heavy_bid(market):
    bids = [BookLevel(price=0.49, size=5000)]
    asks = [BookLevel(price=0.51, size=500)]
    book = OrderBook(token_id="tok_yes", ts=NOW, bids=bids, asks=asks)
    s = OrderBookImbalance(entry_imbalance=0.5)
    sigs = s.on_view(_view(market, price=0.5, book=book))
    assert len(sigs) == 1 and sigs[0].side == Side.BUY


def test_obi_silent_on_wide_spread(market):
    bids = [BookLevel(price=0.30, size=5000)]
    asks = [BookLevel(price=0.70, size=100)]
    book = OrderBook(token_id="tok_yes", ts=NOW, bids=bids, asks=asks)
    assert OrderBookImbalance().on_view(_view(market, price=0.5, book=book)) == []


def test_complement_arb_detects_sub_dollar_pair(market):
    yes = OrderBook(
        token_id="tok_yes", ts=NOW,
        bids=[BookLevel(price=0.40, size=100)], asks=[BookLevel(price=0.45, size=100)],
    )
    no = OrderBook(
        token_id="tok_no", ts=NOW,
        bids=[BookLevel(price=0.48, size=100)], asks=[BookLevel(price=0.50, size=100)],
    )
    s = ComplementArbitrage(min_edge=0.01)
    sigs = s.on_view(_view(market, price=0.42, book=yes, extra={"no_book": no}))
    assert len(sigs) == 2  # buy both legs
    assert all(x.side == Side.BUY for x in sigs)
    assert abs(sigs[0].edge - 0.05) < 1e-9  # 1 - (0.45+0.50)


def test_complement_arb_silent_when_fair(market):
    yes = OrderBook(
        token_id="tok_yes", ts=NOW,
        bids=[BookLevel(price=0.49, size=10)], asks=[BookLevel(price=0.51, size=10)],
    )
    no = OrderBook(
        token_id="tok_no", ts=NOW,
        bids=[BookLevel(price=0.49, size=10)], asks=[BookLevel(price=0.51, size=10)],
    )
    s = ComplementArbitrage()
    assert s.on_view(_view(market, price=0.5, book=yes, extra={"no_book": no})) == []
