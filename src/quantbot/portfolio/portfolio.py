"""Portfolio accounting: cash, positions, mark-to-market equity, PnL.

Convention: positions are share counts of specific tokens (YES or NO).
"Selling YES" without inventory is executed by the broker as buying NO;
the portfolio itself only ever holds long token positions, mirroring how
Polymarket positions actually settle.
"""
from __future__ import annotations

from datetime import datetime

from quantbot.core.types import Fill, Position, Side


class Portfolio:
    def __init__(self, initial_cash: float):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.positions: dict[str, Position] = {}  # token_id -> Position
        self.equity_curve: list[tuple[datetime, float]] = []
        self.peak_equity = initial_cash

    def apply_fill(self, fill: Fill) -> None:
        pos = self.positions.setdefault(
            fill.token_id,
            Position(token_id=fill.token_id, condition_id=fill.condition_id),
        )
        if fill.side == Side.BUY:
            cost = fill.price * fill.size + fill.fee
            self.cash -= cost
            new_size = pos.size + fill.size
            if new_size > 0:
                pos.avg_price = (pos.avg_price * pos.size + fill.price * fill.size) / new_size
            pos.size = new_size
        else:  # SELL from inventory
            sell_size = min(fill.size, pos.size)
            self.cash += fill.price * sell_size - fill.fee
            pos.realized_pnl += (fill.price - pos.avg_price) * sell_size
            pos.size -= sell_size
            if pos.size <= 1e-9:
                pos.size = 0.0
                pos.avg_price = 0.0

    def resolve(self, token_id: str, payout: float, ts: datetime) -> None:
        """Settle a token at resolution (payout is 0.0 or 1.0 per share)."""
        pos = self.positions.get(token_id)
        if pos is None or pos.size <= 0:
            return
        self.cash += payout * pos.size
        pos.realized_pnl += (payout - pos.avg_price) * pos.size
        pos.size = 0.0
        pos.avg_price = 0.0

    def equity(self, marks: dict[str, float]) -> float:
        """Mark-to-market equity. Unmarked positions carry at avg price."""
        val = self.cash
        for tid, pos in self.positions.items():
            if pos.size > 0:
                val += pos.size * marks.get(tid, pos.avg_price)
        return val

    def record_equity(self, ts: datetime, marks: dict[str, float]) -> float:
        eq = self.equity(marks)
        self.equity_curve.append((ts, eq))
        self.peak_equity = max(self.peak_equity, eq)
        return eq

    def exposure(self, marks: dict[str, float]) -> float:
        return sum(
            pos.size * marks.get(tid, pos.avg_price)
            for tid, pos in self.positions.items()
            if pos.size > 0
        )

    def per_market_notional(self, marks: dict[str, float]) -> dict[str, float]:
        out: dict[str, float] = {}
        for tid, pos in self.positions.items():
            if pos.size > 0:
                out[pos.condition_id] = out.get(pos.condition_id, 0.0) + pos.size * marks.get(
                    tid, pos.avg_price
                )
        return out
