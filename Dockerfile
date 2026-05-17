FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[dashboard]"

COPY .env.example ./
COPY scripts ./scripts

RUN useradd -m -u 10001 edge \
    && mkdir -p /app/runtime \
    && chown -R edge:edge /app
USER edge

ENV RUNTIME_DIR=/app/runtime \
    JOURNAL_DB=/app/runtime/journal.sqlite3 \
    AUDIT_LOG=/app/runtime/audit.jsonl \
    DASHBOARD_PATH=/app/runtime/dashboard.txt

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD test -f /app/runtime/dashboard.txt || exit 1

ENTRYPOINT ["edge-bot"]
CMD ["run"]
