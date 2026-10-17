# --- build: resolve locked deps into a venv (deps layer cached separately from source) ---
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# --- runtime: venv only, no uv, no source, non-root ---
FROM python:3.12-slim-bookworm

RUN useradd --create-home --uid 1000 crawler \
    && mkdir /data \
    && chown crawler:crawler /data

COPY --from=builder --chown=crawler:crawler /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /data
USER crawler
VOLUME ["/data"]

ENTRYPOINT ["crawler"]
CMD ["--help"]
