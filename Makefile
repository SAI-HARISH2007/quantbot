.PHONY: install test lint typecheck collect backtest paper clean

install:
	pip install -e ".[dev]"

test:
	pytest -q

lint:
	ruff check src tests

typecheck:
	mypy src/quantbot --ignore-missing-imports

collect:
	quantbot markets sync --min-liquidity 5000
	quantbot collect crypto --days 30
	quantbot collect history --days 30

backtest:
	quantbot backtest run mean_reversion

paper:
	quantbot paper run --top 10 --poll 30

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache **/__pycache__
