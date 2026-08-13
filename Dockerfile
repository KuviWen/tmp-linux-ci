FROM python:3.12.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONPATH=/app/src

RUN groupadd --system app && useradd --system --gid app --create-home app

WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN python -m pip install --no-cache-dir -r requirements.lock

COPY src ./src
COPY migrations ./migrations
COPY openapi ./openapi
COPY alembic.ini dagster-workspace.yaml ./
RUN mkdir -p /var/lib/stock-forecasting/objects /var/lib/dagster \
    && chown -R app:app /var/lib/stock-forecasting /var/lib/dagster

USER app

CMD ["uvicorn", "stock_forecasting.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
