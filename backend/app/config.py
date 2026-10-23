"""Application settings, loaded from environment variables / the repository's .env.

Secrets (DATABASE_URL, GEMINI_API_KEY, ADMIN_API_TOKEN, SUPABASE_JWT_SECRET, BRAVE_SEARCH_API_KEY) are only read
here and never logged. See ../../.env.example for every variable.
"""
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # repository root
DB_SCHEMA = "smartbuy"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", PROJECT_ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    database_url: SecretStr | None = None

    # LLM
    llm_provider: str = "gemini"            # gemini | none
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-2.5-flash"
    llm_timeout_seconds: float = 45.0

    # Auth
    admin_api_token: SecretStr | None = None
    supabase_url: str | None = None
    supabase_jwt_secret: SecretStr | None = None   # only for projects still on the legacy shared JWT secret
    admin_emails: str = ""                          # comma separated; these accounts become admins on sign-in
    cors_origins: str = "http://localhost:3000"

    # Registry files
    products_config: Path = PROJECT_ROOT / "config" / "products.yaml"
    sale_events_config: Path = PROJECT_ROOT / "config" / "sale_events.yaml"

    # Extraction limits
    http_timeout_seconds: float = 20.0
    max_response_bytes: int = 5_000_000
    max_redirects: int = 5
    user_agent: str = "SmartBuyAI/0.1 (+price research; contact: site owner via repository)"
    extra_allowed_hosts: str = ""           # comma separated, in addition to registered source hosts
    per_domain_min_interval_seconds: float = 5.0
    browser_timeout_seconds: float = 60.0
    collection_concurrency: int = 2
    collection_max_retries: int = 3

    # On-demand vendor cache TTLs (hours) - configurable defaults, not validity guarantees
    ttl_emi_hours: float = 12
    ttl_vendor_price_hours: float = 1
    ttl_review_aggregate_hours: float = 24
    ttl_review_samples_hours: float = 24 * 7
    offer_expiry_warning_hours: float = 24

    # Alerts
    alert_cooldown_hours: float = 24
    significant_drop_percent: float = 5.0

    default_collection_interval_minutes: int = Field(default=360, ge=30)

    # Seller discovery (app.workers.discovery): marketplace sub-sellers + vendor sites found by web search
    brave_search_api_key: SecretStr | None = None    # empty = web search skipped, marketplace sellers still tracked
    # Hard cap on Brave queries per calendar month (UTC), counted in the database before each call. Brave's Search
    # plan gives $5 free credit a month = 1,000 queries at $5 / 1,000; 800 leaves a margin so the card is never charged.
    brave_monthly_query_limit: int = Field(default=800, ge=0)
    seller_inactive_days: float = Field(default=30, gt=0)   # not in stock this long -> discovered seller/source deactivated
    discovery_search_count: int = Field(default=20, ge=1, le=20)     # results per search query (Brave max 20)
    discovery_max_checks_per_product: int = Field(default=8, ge=0)  # new candidate pages verified per product per run
    discovery_recheck_rejected_days: float = 14      # a rejected page is not fetched again before this
    discovery_blocked_hosts: str = ""                # comma separated, in addition to the built-in non-shop list
    discovery_read_images: bool = True               # OCR the product images when the page text has no pack size
    discovery_max_images: int = Field(default=3, ge=0, le=10)       # images read per candidate page

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    @property
    def extra_allowed_host_set(self) -> set[str]:
        return {h.strip().lower() for h in self.extra_allowed_hosts.split(",") if h.strip()}

    @property
    def discovery_blocked_host_set(self) -> set[str]:
        return {h.strip().lower() for h in self.discovery_blocked_hosts.split(",") if h.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
