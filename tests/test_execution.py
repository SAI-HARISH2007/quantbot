from datetime import datetime, timezone

from quantbot.config import CostConfig
from quantbot.core.types import BookLevel, Order, OrderBook, OrderType, Side
from quantbot.execution.paper import fill_against_book

NOW = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _book() -> OrderBook:
    return OrderBook(
        token_id="t", ts=NOW,
        bids=[BookLevel(price=0.48, size=100), BookLevel(price=0.47, size=200)],
        asks=[BookLevel(price=0.52, size=100), BookLevel(price=0.54, size=200)],
    )


def _order(side: Side, price: float, size: float) -> Order:
    return Order(
        order_id="o", token_id="t", condition_id="c", side=side,
        order_type=OrderType.LIMIT, price=price, size=size, ts=NOW,
    )


def test_buy_walks_ask_levels():
    costs = CostConfig(extra_slippage=0.0)
    fill = fill_against_book(_order(Side.BUY, price=0.55, size=150), _book(), costs)
    assert fill is not None
    assert abs(fill.size - 150) < 1e-9
    # 100 @ .52 + 50 @ .54 = avg .5267
    assert abs(fill.price - (100 * 0.52 + 50 * 0.54) / 150) < 1e-9


def test_buy_respects_limit_price():
    costs = CostConfig(extra_slippage=0.0)
    fill = fill_against_book(_order(Side.BUY, price=0.52, size=150), _book(), costs)
    assert fill is not None
    assert abs(fill.size - 100) < 1e-9  # second level exceeds limit


def test_no_fill_when_limit_below_market():
    fill = fill_against_book(_order(Side.BUY, price=0.40, size=10), _book(), CostConfig())
    assert fill is None


def test_slippage_moves_against_trader():
    costs = CostConfig(extra_slippage=0.01)
    buy = fill_against_book(_order(Side.BUY, price=0.55, size=50), _book(), costs)
    sell = fill_against_book(_order(Side.SELL, price=0.40, size=50), _book(), costs)
    assert buy.price > 0.52  # pays more
    assert sell.price < 0.48  # receives less


def test_fees_applied():
    costs = CostConfig(taker_fee_bps=100.0, extra_slippage=0.0)  # 1%
    fill = fill_against_book(_order(Side.BUY, price=0.55, size=100), _book(), costs)
    assert abs(fill.fee - 0.52) < 1e-9  # 1% of 100*0.52
