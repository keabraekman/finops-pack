# finops-pack

Starter scaffold for a Python CLI project using:
- uv for environment and dependency management
- Ruff for linting and formatting
- mypy for type checking
- pytest for tests

## Setup

```bash
# install uv first if needed
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# create env + install dev dependencies
uv sync --dev
```

## Commands

```bash
uv run ruff check .
uv run ruff format .
uv run mypy .
uv run pytest
uv run finops-pack
```
