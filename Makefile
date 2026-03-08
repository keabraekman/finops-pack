.PHONY: lint format typecheck test check run

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy .

test:
	uv run pytest

check: lint typecheck test

run:
	uv run python -m finops_pack.cli