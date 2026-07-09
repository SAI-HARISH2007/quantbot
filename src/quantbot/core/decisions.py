"""Decision records: the explainability backbone.

Every signal the system evaluates produces exactly one DecisionRecord —
whether it became a trade or was rejected. When a position closes, a
TradeCloseReport links back to its entry decisions. Nothing the trader
sees on the dashboard is reconstructed after the fact; it is the actual
data the system decided with.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from quantbot.core.types import utcnow


class DecisionRecord(BaseModel):
    decision_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    ts: datetime = Field(default_factory=utcnow)
    run_id: str = ""
    # --- the signal ---
    strategy: str = ""
    condition_id: str = ""
    token_id: str = ""
    market_question: str = ""
    side: str = ""
    signal_edge: float = 0.0
    signal_confidence: float = 0.0
    signal_metadata: dict = Field(default_factory=dict)
    # --- the evidence ---
    market_price: Optional[float] = None
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    spread: Optional[float] = None
    book_imbalance: Optional[float] = None
    fair_value: Optional[float] = None          # ensemble P(YES)
    fair_value_std: Optional[float] = None      # ensemble uncertainty
    fair_value_models: dict = Field(default_factory=dict)  # model -> {prob, std}
    expected_value: Optional[float] = None      # edge per share, prob units
    # --- the sizing math ---
    sizing: dict = Field(default_factory=dict)  # full breakdown from size_order_explain
    # --- the risk verdict ---
    risk_ok: bool = False
    risk_reason: str = ""
    risk_state: dict = Field(default_factory=dict)
    # --- the outcome ---
    outcome: str = "rejected"  # rejected | unfilled | filled
    order_id: Optional[str] = None
    limit_price: Optional[float] = None
    fill_price: Optional[float] = None
    fill_size: Optional[float] = None
    fee: float = 0.0
    slippage: Optional[float] = None  # fill price vs decision-time reference
    # --- exit policy (declared at entry, so it is never invented later) ---
    exit_policy: str = (
        "Exits on: opposite signal from the same strategy, market resolution, "
        "or risk kill switch. Loss is bounded by position size (max loss = "
        "entry cost); no separate stop-loss order exists on Polymarket."
    )


class TradeCloseReport(BaseModel):
    report_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    ts: datetime = Field(default_factory=utcnow)
    run_id: str = ""
    token_id: str = ""
    condition_id: str = ""
    market_question: str = ""
    strategy: str = ""
    entry_decision_ids: list[str] = Field(default_factory=list)
    entry_ts: Optional[datetime] = None
    entry_price: float = 0.0
    exit_price: float = 0.0
    size: float = 0.0
    pnl: float = 0.0
    fees: float = 0.0
    holding_hours: float = 0.0
    exit_reason: str = ""  # opposite_signal | resolution | kill_switch | manual
    # honest self-assessment
    entry_fair_value: Optional[float] = None
    hypothesis: str = ""            # what the entry predicted, in words
    hypothesis_correct: Optional[bool] = None  # None = market not resolved yet
    lessons: str = ""               # rule-generated commentary
