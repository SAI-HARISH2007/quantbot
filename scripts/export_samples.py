"""Export a small, committable sample dataset from the local store.

Usage: python scripts/export_samples.py
Writes CSVs under data/samples/ (whitelisted in .gitignore) so tests, docs,
and new checkouts have real-shaped data without hitting the network.
"""
from __future__ import annotations

from pathlib import Path

from quantbot.config import load_config
from quantbot.data.storage import Store


def main() -> None:
    cfg = load_config()
    store = Store(cfg.storage)
    out = Path("data/samples")
    out.mkdir(parents=True, exist_ok=True)

    markets = sorted(store.load_markets(), key=lambda m: -m.liquidity)[:20]
    import pandas as pd

    pd.DataFrame([m.model_dump() for m in markets]).to_csv(
        out / "markets_sample.csv", index=False
    )

    n_series = 0
    for m in markets:
        df = store.load_price_history(m.yes_token_id)
        if len(df) >= 50 and n_series < 5:
            df.to_csv(out / f"pm_prices_{m.slug[:40]}.csv", index=False)
            n_series += 1

    for sym in cfg.crypto.symbols:
        df = store.load_candles(sym)
        if len(df):
            df.tail(2000).to_csv(out / f"candles_{sym}.csv", index=False)

    print(f"wrote samples to {out}/ ({n_series} price series, {len(markets)} markets)")


if __name__ == "__main__":
    main()
