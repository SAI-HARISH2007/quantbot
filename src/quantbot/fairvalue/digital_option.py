"""Digital option pricing for crypto threshold markets.

A market like "Will BTC be above $65,000 on July 31?" is a cash-or-nothing
digital call on BTC. Under GBM with zero drift (risk-neutral, r≈0 in USDC
terms), P(S_T > K) = N(d2):

    d2 = (ln(S/K) - 0.5·σ²·T) / (σ·√T)

The strike and direction are parsed from the market question. Vol comes from
a pluggable realized-vol estimator over the underlying candles. This model
is a hypothesis; its calibration is measured against resolved outcomes.
"""
from __future__ import annotations

import math
import re
from typing import Optional

from scipy.stats import norm

from quantbot.core.types import MarketInfo
from quantbot.fairvalue.base import FairValueContext, FairValueEstimate, FairValueModel
from quantbot.fairvalue.vol import estimate_vol

_ASSET_PAT = re.compile(r"\b(bitcoin|btc|ethereum|eth)\b", re.IGNORECASE)
_PRICE_PAT = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*([kKmM]?)")
_UP_WORDS = re.compile(r"\b(above|reach|hit|exceed|higher|over|greater)\b", re.IGNORECASE)
_DOWN_WORDS = re.compile(r"\b(below|under|less|lower|dip|drop|fall)\b", re.IGNORECASE)

_SYMBOLS = {"bitcoin": "BTCUSDT", "btc": "BTCUSDT", "ethereum": "ETHUSDT", "eth": "ETHUSDT"}


def parse_threshold_market(market: MarketInfo) -> Optional[dict]:
    """Extract (symbol, strike, direction) from a market question.
    Returns None if the question is not a recognizable threshold market."""
    text = market.question
    asset = _ASSET_PAT.search(text)
    price = _PRICE_PAT.search(text)
    if not asset or not price:
        return None
    strike = float(price.group(1).replace(",", ""))
    suffix = price.group(2).lower()
    if suffix == "k":
        strike *= 1_000
    elif suffix == "m":
        strike *= 1_000_000
    if _DOWN_WORDS.search(text) and not _UP_WORDS.search(text):
        direction = "below"
    elif _UP_WORDS.search(text):
        direction = "above"
    else:
        return None
    return {
        "symbol": _SYMBOLS[asset.group(1).lower()],
        "strike": strike,
        "direction": direction,
    }


def digital_call_prob(spot: float, strike: float, vol: float, t_years: float) -> float:
    """P(S_T > K) under zero-drift GBM."""
    if t_years <= 0:
        return 1.0 if spot > strike else 0.0
    if vol <= 0 or spot <= 0 or strike <= 0:
        return 1.0 if spot > strike else 0.0
    d2 = (math.log(spot / strike) - 0.5 * vol * vol * t_years) / (vol * math.sqrt(t_years))
    return float(norm.cdf(d2))


class DigitalOptionModel(FairValueModel):
    name = "digital_option"

    def __init__(self, vol_method: str = "ewma", vol_multiplier: float = 1.0):
        # vol_multiplier lets research sweep systematic over/under-estimation
        self.vol_method = vol_method
        self.vol_multiplier = vol_multiplier

    def estimate(self, ctx: FairValueContext) -> Optional[FairValueEstimate]:
        parsed = parse_threshold_market(ctx.market)
        if not parsed or ctx.market.end_date is None:
            return None
        # Resolve the correct underlying series for the parsed symbol.
        candles = ctx.candles_by_symbol.get(parsed["symbol"], None)
        if candles is not None and len(candles):
            spot = float(candles["close"].iloc[-1])
        else:
            candles, spot = ctx.candles, ctx.spot
        if spot is None or candles is None or len(candles) < 30:
            return None
        t_years = (ctx.market.end_date - ctx.now).total_seconds() / (365.25 * 86400)
        if t_years < 0:
            return None
        vol = estimate_vol(candles, self.vol_method) * self.vol_multiplier
        if vol <= 0:
            return None
        p_above = digital_call_prob(spot, parsed["strike"], vol, t_years)
        prob = p_above if parsed["direction"] == "above" else 1.0 - p_above
        # Uncertainty: vega of a digital wrt vol misspecification (~30% rel err on vol)
        bump = digital_call_prob(spot, parsed["strike"], vol * 1.3, t_years)
        std = max(abs(bump - p_above), 0.01)
        return FairValueEstimate(
            model=self.name,
            prob=prob,
            std=std,
            detail={
                "spot": spot,
                "strike": parsed["strike"],
                "direction": parsed["direction"],
                "vol": vol,
                "t_years": t_years,
            },
        ).clamped()
