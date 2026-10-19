# SmartBuy AI backend: API (default) or collection worker (override the command).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt \
 && playwright install --with-deps chromium \
 && useradd --create-home --uid 10001 smartbuy

COPY backend/app ./app
COPY config /config
COPY supabase /supabase
COPY scripts /scripts
ENV PRODUCTS_CONFIG=/config/products.yaml SALE_EVENTS_CONFIG=/config/sale_events.yaml PYTHONPATH=/app

USER smartbuy
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
