.PHONY: lint format typecheck test build check run web worker preview

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

web:
	uv run finops-pack-web

worker:
	uv run finops-pack-worker

preview:
	cd out && python -m http.server
