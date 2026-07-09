from quantbot.core.types import OrderBook


def test_book_derived_quantities(book: OrderBook):
    assert book.best_bid.price == 0.48
    assert book.best_ask.price == 0.52
    assert abs(book.mid - 0.50) < 1e-9
    assert abs(book.spread - 0.04) < 1e-9


def test_imbalance_sign(book: OrderBook):
    # bids 3000 vs asks 2300 -> positive imbalance
    imb = book.imbalance()
    assert imb is not None and imb > 0


def test_microprice_leans_toward_thin_side(book: OrderBook):
    # ask size (800) < bid size (1000) at top -> microprice above mid
    mp = book.microprice()
    assert mp is not None and mp > book.mid


def test_empty_book_is_safe():
    from datetime import datetime, timezone

    b = OrderBook(token_id="x", ts=datetime.now(timezone.utc))
    assert b.mid is None and b.spread is None and b.imbalance() is None
