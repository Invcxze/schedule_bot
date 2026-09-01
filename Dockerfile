FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /uvx /bin/
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock .python-version README.md ./
COPY schedule_bot ./schedule_bot
RUN uv sync --locked --no-dev --no-editable --no-cache \
    && useradd --uid 10001 --create-home bot \
    && mkdir /app/data && chown bot:bot /app/data
USER bot
CMD ["uv", "run", "--no-sync", "schedule-bot", "run"]
