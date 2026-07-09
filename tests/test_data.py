import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quantbot.config import StorageConfig
from quantbot.core.types import Candle, Fill, PricePoint, Side
from quantbot.data.polymarket.gamma import parse_market
from quantbot.data.polymarket.ws import _parse_book_msg
from quantbot.data.storage import Store

NOW = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _gamma_raw(**over) -> dict:
    base = {
        "conditionId": "0xabc",
        "question": "Will BTC be above $65,000?",
        "slug": "btc-65k",
        "clobTokenIds": json.dumps(["111", "222"]),
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps(["0.51", "0.49"]),
        "endDate": "2026-07-31T12:00:00Z",
        "active": True,
        "closed": False,
        "liquidity": "12345.6",
        "volume": "99999.9",
    }
    base.update(over)
    return base


def test_parse_market_happy_path():
    m = parse_market(_gamma_raw())
    assert m is not None
    assert m.yes_token_id == "111" and m.no_token_id == "222"
    assert m.end_date.tzinfo is not None
    assert m.liquidity == 12345.6


def test_parse_market_rejects_multi_outcome():
    raw = _gamma_raw(
        clobTokenIds=json.dumps(["1", "2", "3"]),
        outcomes=json.dumps(["A", "B", "C"]),
    )
    assert parse_market(raw) is None


def test_parse_market_survives_garbage():
    assert parse_market({"clobTokenIds": "not json"}) is None
    assert parse_market({}) is None


def test_ws_book_message_parsing():
    msg = {
        "event_type": "book",
        "asset_id": "tok1",
        "timestamp": str(int(NOW.timestamp() * 1000)),
        "bids": [{"price": "0.47", "size": "100"}, {"price": "0.48", "size": "50"}],
        "asks": [{"price": "0.53", "size": "60"}, {"price": "0.52", "size": "40"}],
    }
    book = _parse_book_msg(msg)
    assert book is not None
    assert book.best_bid.price == 0.48  # sorted desc
    assert book.best_ask.price == 0.52  # sorted asc
    assert _parse_book_msg({"event_type": "price_change"}) is None


def test_store_roundtrips(tmp_path: Path):
    store = Store(StorageConfig(root=tmp_path))
    m = parse_market(_gamma_raw())
    store.upsert_markets([m])
    loaded = store.load_markets(active_only=True)
    assert len(loaded) == 1 and loaded[0].condition_id == "0xabc"

    pts = [
        PricePoint(token_id="111", ts=NOW + timedelta(minutes=i), price=0.5 + i / 100)
        for i in range(5)
    ]
    store.save_price_history(pts)
    store.save_price_history(pts)  # idempotent
    df = store.load_price_history("111")
    assert len(df) == 5

    candles = [
        Candle(symbol="BTCUSDT", ts=NOW + timedelta(minutes=i),
               open=1, high=2, low=0.5, close=1.5, volume=10)
        for i in range(3)
    ]
    store.save_candles(candles)
    assert len(store.load_candles("BTCUSDT")) == 3

    fill = Fill(order_id="o1", token_id="111", condition_id="0xabc",
                side=Side.BUY, price=0.5, size=10, ts=NOW)
    store.save_fill(fill, run_id="r1")
    fills = store.load_fills("r1")
    assert len(fills) == 1 and fills.iloc[0]["side"] == "BUY"
    store.close()
