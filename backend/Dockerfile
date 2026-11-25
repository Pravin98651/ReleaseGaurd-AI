FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system --gid 1001 appgroup \
    && adduser --system --uid 1001 --ingroup appgroup appuser \
    && chown appuser:appgroup /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appgroup alembic.ini ./
COPY --chown=appuser:appgroup alembic ./alembic
COPY --chown=appuser:appgroup app ./app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"]

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
