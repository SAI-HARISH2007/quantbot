# Deployment

## Local / server (paper trading)

```bash
git clone <repo> && cd quantbot
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                      # verify environment

quantbot markets sync --min-liquidity 5000
quantbot collect crypto --days 30
quantbot collect history --days 30

# long-running collectors + paper trader (e.g. under tmux/systemd)
quantbot collect books --interval 30 &
quantbot paper run --top 10 --poll 30
```

### systemd unit (example)

```ini
[Unit]
Description=quantbot paper trader
After=network-online.target

[Service]
WorkingDirectory=/opt/quantbot
ExecStart=/opt/quantbot/.venv/bin/quantbot paper run --top 10
Restart=on-failure
RestartSec=30
Environment=QUANTBOT_LOG_LEVEL=INFO

[Install]
WantedBy=multi-user.target
```

Logs rotate under `logs/quantbot.log`; fills and equity persist in
`data/quantbot.db` keyed by run id; experiment records land in
`experiments/runs/*.json`.

## Configuration

All settings load from `configs/default.yaml`, overridable per-key via env:

```bash
QUANTBOT_RISK__KELLY_FRACTION=0.1 QUANTBOT_RISK__MAX_TOTAL_EXPOSURE=1000 \
  quantbot paper run
```

## Live trading (gated — read docs/RESEARCH.md promotion criteria first)

1. `pip install -e ".[live]"` (installs `py-clob-client`).
2. Fund a Polygon wallet with USDC; export credentials:
   ```bash
   export QUANTBOT_PM_PRIVATE_KEY=0x...
   ```
3. Live execution requires constructing `LiveBroker(allow_live=True)` —
   there is intentionally no CLI flag for this yet; wiring it into the
   runner is a deliberate, reviewed code change, not a config toggle.
4. Start with `max_total_exposure` ≤ $100 and compare live fills against
   the paper broker's predictions before scaling.

## Operational safety checklist

- [ ] Kill switch threshold (`risk.max_drawdown_pct`) set and tested
- [ ] Per-market and total exposure caps sized to bankroll
- [ ] Monitoring on equity curve and error logs (rotating file handler)
- [ ] Wallet holds only the capital you intend to trade
- [ ] Paper run id archived alongside the config used
