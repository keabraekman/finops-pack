.PHONY: lint format typecheck test build check run preview

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy .

test:
	uv run pytest

build:
	uv build

check: lint typecheck test

run:
	uv run python -m finops_pack.cli demo

preview:
	cd out && python -m http.server
