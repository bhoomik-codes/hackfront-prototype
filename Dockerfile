FROM node:22-bookworm-slim AS frontend

WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv \
    && useradd --create-home --uid 10001 appuser

WORKDIR /app
COPY server/ /app/server/
RUN uv sync --project /app/server --no-dev --no-install-project
COPY --from=frontend /web/dist /app/dist
RUN chown -R appuser:appuser /app

USER appuser
WORKDIR /app/server
EXPOSE 10000
CMD ["sh", "-c", "uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --no-access-log"]
