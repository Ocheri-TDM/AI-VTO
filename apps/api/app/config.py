from decimal import Decimal
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore"
    )
    database_url: str = "postgresql+asyncpg://souvenir:souvenir@127.0.0.1:5432/souvenir"
    rub_kzt_rate: Decimal | None = Field(default=None, gt=0)
    cache_ttl_seconds: int = Field(default=3600, ge=60, le=3600)
    browser_headless: bool = True
    browser_timeout_ms: int = Field(default=30000, ge=1000, le=120000)
    browser_concurrency: int = Field(default=2, ge=1, le=8)
    provider_timeout_seconds: int = Field(default=240, ge=10, le=900)
    provider_retries: int = Field(default=1, ge=0, le=2)
    max_pages_per_query: int = Field(default=2, ge=1, le=10)
    max_products_per_query: int = Field(default=12, ge=1, le=100)
    max_active_searches: int = Field(default=4, ge=1, le=20)
    research_supplier_concurrency: int = Field(default=2, ge=1, le=6)
    research_request_delay_seconds: float = Field(default=0.8, ge=0.2, le=30)
    research_provider_delays: dict[str, float] = Field(default_factory=dict)
    research_max_seconds: int = Field(default=600, ge=10, le=14400)
    research_branch_slice_seconds: int = Field(default=45, ge=5, le=300)
    background_research_enabled: bool = True
    observation_refresh_minutes: int = Field(default=120, ge=1, le=10080)
    hot_refresh_hours: int = Field(default=2, ge=1, le=24)
    warm_refresh_hours: int = Field(default=6, ge=1, le=168)
    cold_refresh_hours: int = Field(default=24, ge=1, le=720)
    warm_usage_days: int = Field(default=7, ge=1, le=90)
    dormant_usage_days: int = Field(default=30, ge=1, le=365)
    incremental_sync_hours: int = Field(default=24, ge=1, le=720)
    full_reconciliation_days: int = Field(default=7, ge=1, le=90)
    catalog_discovery_interval_hours: int = Field(default=168, ge=1, le=2160)
    supplier_failure_backoff_minutes: int = Field(default=15, ge=1, le=1440)
    supplier_failure_backoff_max_hours: int = Field(default=4, ge=1, le=168)
    supplier_circuit_failure_threshold: int = Field(default=3, ge=2, le=20)
    targeted_refresh_limit: int = Field(default=24, ge=1, le=200)
    global_research_concurrency: int = Field(default=2, ge=1, le=6)
    provider_concurrency: int = Field(default=1, ge=1, le=2)
    browser_page_limit: int = Field(default=2, ge=1, le=8)
    research_job_timeout_seconds: int = Field(default=300, ge=10, le=14400)
    targeted_provider_timeout_seconds: int = Field(default=20, ge=5, le=60)
    targeted_slice_products: int = Field(default=12, ge=1, le=100)
    background_slice_products: int = Field(default=8, ge=1, le=100)
    scheduler_stagger_seconds: int = Field(default=300, ge=0, le=3600)
    llm_backend: str = "basic"
    llm_base_url: str = "http://127.0.0.1:11434"
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=60, ge=1, le=300)
    llm_structured_retries: int = Field(default=1, ge=0, le=2)
    max_tool_calls_per_message: int = Field(default=5, ge=1, le=5)
    agent_timeout_seconds: float = Field(default=600, ge=10, le=1800)
    debug_agent: bool = False
    auth_required: bool = True
    auth_cookie_secure: bool = False
    auth_session_days: int = Field(default=30, ge=1, le=365)
    api_host: str = "127.0.0.1"
    api_port: int = 8000
