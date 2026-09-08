FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /usr/local/bin/uv

WORKDIR /srv

# Dependencies first so a code change does not reinstall the world.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app

# Lives on the mounted volume, not in the image: a redeploy must not wipe the log.
ENV WORKSPACE_DB=/data/workspace.db
ENV PATH="/srv/.venv/bin:$PATH"

EXPOSE 8080
# Shell form on purpose: Railway injects $PORT, Fly expects 8080. This works on both.
CMD ["sh", "-c", "uvicorn app.server:app --host 0.0.0.0 --port ${PORT:-8080}"]
