"""Live execution via Polymarket CLOB — intentionally gated.

Live trading requires:
1. `pip install py-clob-client` (the `live` extra)
2. A funded Polygon wallet + Polymarket API credentials in env vars:
   QUANTBOT_PM_PRIVATE_KEY, QUANTBOT_PM_API_KEY, QUANTBOT_PM_API_SECRET,
   QUANTBOT_PM_API_PASSPHRASE
3. Explicitly setting ``allow_live=True``

The class fails loudly rather than trading silently. Live enablement should
come only after paper results clear the promotion criteria in
docs/RESEARCH.md (positive walk-forward Sharpe with CI excluding 0).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from quantbot.core.types import Fill, Order, Side

logger = logging.getLogger(__name__)


class LiveBroker:
    def __init__(self, allow_live: bool = False):
        if not allow_live:
            raise RuntimeError(
                "LiveBroker requires allow_live=True. Run paper trading first; "
                "see docs/RESEARCH.md for promotion criteria."
            )
        try:
            from py_clob_client.client import ClobClient as _PyClob  # type: ignore
        except ImportError as e:
            raise RuntimeError("pip install 'quantbot[live]' to enable live trading") from e
        key = os.environ.get("QUANTBOT_PM_PRIVATE_KEY")
        if not key:
            raise RuntimeError("QUANTBOT_PM_PRIVATE_KEY not set")
        self._client = _PyClob(
            "https://clob.polymarket.com",
            key=key,
            chain_id=137,
        )
        self._client.set_api_creds(self._client.create_or_derive_api_creds())
        logger.warning("LIVE trading enabled — real funds at risk")

    async def submit(self, order: Order) -> Optional[Fill]:
        from py_clob_client.clob_types import OrderArgs, OrderType as _OT  # type: ignore
        from py_clob_client.order_builder.constants import BUY, SELL  # type: ignore

        args = OrderArgs(
            price=round(order.price, 3),
            size=round(order.size, 2),
            side=BUY if order.side == Side.BUY else SELL,
            token_id=order.token_id,
        )
        signed = self._client.create_order(args)
        resp = self._client.post_order(signed, _OT.GTC)
        logger.info("live order response: %s", resp)
        if not resp.get("success"):
            return None
        # Live fills arrive asynchronously via the user channel; treating the
        # ack as a fill at limit price is a simplification to refine with the
        # user websocket before scaling size.
        return Fill(
            order_id=str(resp.get("orderID", order.order_id)),
            token_id=order.token_id,
            condition_id=order.condition_id,
            side=order.side,
            price=order.price,
            size=order.size,
            ts=order.ts,
            strategy=order.strategy,
        )
