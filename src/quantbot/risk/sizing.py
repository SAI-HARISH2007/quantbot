"""Position sizing.

Kelly for a binary contract bought at price p with estimated win prob q:
edge per $1 staked pays (1-p)/p on win, loses 1 on loss →
    f* = (q - p) / (p · (1 - p)) · p = (q - p) / (1 - p)   [long YES]
We use *fractional* Kelly (default 25%) shrunk further by signal confidence,
and hard-capped as % of equity. Overbetting is the classic way calibrated
models still blow up; the fraction is a config knob swept in research.
"""
from __future__ import annotations

from quantbot.config import RiskConfig
from quantbot.core.types import MarketInfo, Side, Signal


def as_complement_buy(sig: Signal, market: MarketInfo) -> Signal:
    """Convert a SELL signal on one token into the equivalent BUY of the
    complementary token (how Polymarket short exposure actually works).

    Everything probability-denominated flips into the complement's space:
    fair value q -> 1-q, market price p -> 1-p. Passing an unconverted SELL
    signal to size_order with a complement-space price computes Kelly edge
    across mismatched spaces and mis-sizes badly.
    """
    other = (
        market.no_token_id
        if sig.token_id == market.yes_token_id
        else market.yes_token_id
    )
    return sig.model_copy(
        update={
            "side": Side.BUY,
            "token_id": other,
            "fair_value": None if sig.fair_value is None else 1.0 - sig.fair_value,
            "market_price": None if sig.market_price is None else 1.0 - sig.market_price,
        }
    )


def kelly_fraction_binary(q: float, price: float, side: Side) -> float:
    """Optimal Kelly fraction of bankroll for a binary contract.
    q = believed P(YES); price = market price of YES."""
    p = min(max(price, 1e-4), 1 - 1e-4)
    if side == Side.BUY:
        edge = q - p
        return max(edge / (1.0 - p), 0.0)
    # Selling YES at p == buying NO at (1-p) with win prob (1-q)
    edge = p - q
    return max(edge / p, 0.0)


def size_order(
    signal: Signal,
    equity: float,
    price: float,
    cfg: RiskConfig,
) -> float:
    """Return order size in *shares* (0 = don't trade)."""
    if signal.fair_value is not None:
        f = kelly_fraction_binary(signal.fair_value, price, signal.side)
    else:
        # No probabilistic estimate: fall back to edge-proportional stake
        f = min(signal.edge * 2.0, 0.5)
    f *= cfg.kelly_fraction * min(max(signal.confidence, 0.0), 1.0)
    f = min(f, cfg.max_kelly_stake_pct)
    notional = equity * f
    notional = min(notional, cfg.max_position_per_market)
    if notional < cfg.min_order_notional:
        return 0.0
    cost_per_share = price if signal.side == Side.BUY else (1.0 - price)
    if cost_per_share <= 0:
        return 0.0
    return notional / cost_per_share
