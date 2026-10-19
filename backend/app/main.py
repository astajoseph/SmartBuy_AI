"""FastAPI application. OpenAPI docs at /docs."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import admin, assistant, events, prices, products, recommendations, vendors
from app.config import get_settings
from app.database.client import DatabaseNotConfigured
from app.logging_config import configure_logging

configure_logging()
log = logging.getLogger("app")
settings = get_settings()

app = FastAPI(
    title="SmartBuy AI API",
    version="0.1.0",
    description="Indian-market purchase intelligence: price history, forecasts, sale-event analysis, "
                "on-demand vendor/EMI comparison and an explainable purchase advisor.",
)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False,
                   allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])

for r in (products.router, prices.router, vendors.router, recommendations.router, assistant.router,
          events.router, admin.router):
    app.include_router(r)


@app.exception_handler(DatabaseNotConfigured)
async def db_not_configured(_: Request, exc: DatabaseNotConfigured):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    log.exception("unhandled error")
    return JSONResponse(status_code=500, content={"detail": "Internal error. See server logs."})


@app.get("/api/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "llm_provider": settings.llm_provider,
            "llm_configured": bool(settings.gemini_api_key), "database_configured": bool(settings.database_url),
            "admin_api_enabled": bool(settings.admin_api_token)}
