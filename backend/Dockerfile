FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/telemetry.db \
    WEB_DIR=/app/web

WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
 && useradd --system --uid 10001 --home /app telemetry \
 && mkdir -p /data && chown telemetry /data

COPY backend/app /app/app
COPY web /app/web

USER telemetry
EXPOSE 8791
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8791/healthz').status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8791", "--proxy-headers", "--forwarded-allow-ips", "*"]
