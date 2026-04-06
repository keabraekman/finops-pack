FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY demo ./demo
COPY infra ./infra

RUN pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev

CMD ["finops-pack-worker"]

