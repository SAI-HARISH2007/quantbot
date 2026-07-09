"""Round-trip trade reconstruction from fills + settlements."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd

from quantbot.core.types import Fill, Side


@dataclass
class _OpenLot:
    entry_ts: datetime
    size: float
    price: float
    strategy: str


class TradeLog:
    """FIFO round-trip matching per token. A 'trade' closes when shares are
    sold or the market settles."""

    def __init__(self) -> None:
        self._lots: dict[str, list[_OpenLot]] = {}
        self.records: list[dict] = []

    def on_fill(self, fill: Fill) -> None:
        lots = self._lots.setdefault(fill.token_id, [])
        if fill.side == Side.BUY:
            lots.append(_OpenLot(fill.ts, fill.size, fill.price, fill.strategy))
            return
        remaining = fill.size
        while remaining > 1e-9 and lots:
            lot = lots[0]
            take = min(lot.size, remaining)
            self._close(fill.token_id, lot, take, fill.price, fill.ts)
            lot.size -= take
            remaining -= take
            if lot.size <= 1e-9:
                lots.pop(0)

    def on_settlement(self, token_id: str, payout: float, ts: datetime) -> None:
        for lot in self._lots.pop(token_id, []):
            self._close(token_id, lot, lot.size, payout, ts)

    def _close(
        self, token_id: str, lot: _OpenLot, size: float, exit_price: float, ts: datetime
    ) -> None:
        self.records.append(
            {
                "token_id": token_id,
                "strategy": lot.strategy,
                "entry_ts": lot.entry_ts,
                "exit_ts": ts,
                "entry_price": lot.price,
                "exit_price": exit_price,
                "size": size,
                "notional": size * lot.price,
                "pnl": (exit_price - lot.price) * size,
                "holding_hours": (ts - lot.entry_ts).total_seconds() / 3600,
            }
        )

    def to_frame(self) -> pd.DataFrame:
        if not self.records:
            return pd.DataFrame(
                columns=["token_id", "strategy", "entry_ts", "exit_ts", "entry_price",
                         "exit_price", "size", "notional", "pnl", "holding_hours"]
            )
        return pd.DataFrame(self.records)
