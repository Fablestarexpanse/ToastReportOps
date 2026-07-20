FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src
RUN uv sync --frozen --no-dev

# prompts/ is provided at runtime by the compose bind-mount (./prompts:/app/prompts).
# It lives outside this build context (repo root), so it is not COPYd into the image.

ENV PATH="/app/.venv/bin:$PATH"

# service selected per compose service, e.g.:
#   python -m toast.api.app | python -m toast.queue.worker | python -m toast.scheduler
CMD ["python", "-m", "toast.queue.worker"]
