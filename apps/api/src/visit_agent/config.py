import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_files() -> None:
    """Load local env files without adding a runtime dependency."""
    candidates: list[Path] = []
    cwd = Path.cwd()
    candidates.extend([cwd / ".env.local", cwd / ".env"])
    for parent in cwd.parents:
        candidates.extend([parent / ".env.local", parent / ".env"])
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_files()


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "demo")
    api_host: str = os.getenv("API_HOST", "127.0.0.1")
    api_port: int = int(os.getenv("API_PORT", "8000"))
    web_origins: tuple[str, ...] = tuple(
        item.strip()
        for item in os.getenv("WEB_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(
            ","
        )
        if item.strip()
    )
    database_url: str = os.getenv("DATABASE_URL", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", os.getenv("LLM_API_KEY", ""))
    openai_base_url: str = os.getenv(
        "OPENAI_BASE_URL",
        os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
    )
    openai_model: str = os.getenv("OPENAI_MODEL", os.getenv("LLM_MODEL", "gpt-5-mini"))
    erp_provider: str = os.getenv("ERP_PROVIDER", "erpnext").lower()
    erp_excel_path: str = os.getenv("ERP_EXCEL_PATH", "")
    erp_next_base_url: str = os.getenv("ERP_NEXT_BASE_URL", "")
    erp_next_api_key: str = os.getenv("ERP_NEXT_API_KEY", "")
    erp_next_api_secret: str = os.getenv("ERP_NEXT_API_SECRET", "")
    calendar_provider: str = os.getenv("CALENDAR_PROVIDER", "auto").lower()
    feishu_app_id: str = os.getenv("FEISHU_APP_ID", "")
    feishu_app_secret: str = os.getenv("FEISHU_APP_SECRET", "")
    feishu_base_url: str = os.getenv("FEISHU_BASE_URL", "https://open.feishu.cn/open-apis")
    feishu_calendar_id: str = os.getenv("FEISHU_CALENDAR_ID", "primary")
    feishu_event_verification_token: str = os.getenv("FEISHU_EVENT_VERIFICATION_TOKEN", "")
    feishu_event_encrypt_key: str = os.getenv("FEISHU_EVENT_ENCRYPT_KEY", "")
    microsoft_tenant_id: str = os.getenv("MICROSOFT_TENANT_ID", "")
    microsoft_client_id: str = os.getenv("MICROSOFT_CLIENT_ID", "")
    microsoft_client_secret: str = os.getenv("MICROSOFT_CLIENT_SECRET", "")
    search_provider: str = os.getenv("SEARCH_PROVIDER", "disabled").lower()
    serper_api_key: str = os.getenv("SERPER_API_KEY", "")
    serper_url: str = os.getenv("SERPER_URL", "https://google.serper.dev/search")
    routing_provider: str = os.getenv("ROUTING_PROVIDER", "mock")
    osrm_base_url: str = os.getenv("OSRM_BASE_URL", "http://localhost:5000")
    geocoding_provider: str = os.getenv("GEOCODING_PROVIDER", "mock")
    nominatim_base_url: str = os.getenv(
        "NOMINATIM_BASE_URL",
        "https://nominatim.openstreetmap.org",
    )
    nominatim_user_agent: str = os.getenv("NOMINATIM_USER_AGENT", "routenda-demo")
    smtp_host: str = os.getenv("SMTP_HOST", "localhost")
    smtp_port: int = int(os.getenv("SMTP_PORT", "1025"))
    smtp_from: str = os.getenv("SMTP_FROM", "visit-agent@example.test")
    reminder_first_send_delay_minutes: int = int(
        os.getenv("REMINDER_FIRST_SEND_DELAY_MINUTES", "0")
    )
    reminder_interval_hours: int = int(os.getenv("REMINDER_INTERVAL_HOURS", "24"))
    reminder_max_count: int = int(os.getenv("REMINDER_MAX_COUNT", "2"))
    reminder_quiet_start: str = os.getenv("REMINDER_QUIET_START", "20:00")
    reminder_quiet_end: str = os.getenv("REMINDER_QUIET_END", "08:00")
    reminder_timezone: str = os.getenv("REMINDER_TIMEZONE", "Asia/Shanghai")
    outbox_poll_seconds: float = float(os.getenv("OUTBOX_POLL_SECONDS", "2"))
    outbox_batch_size: int = int(os.getenv("OUTBOX_BATCH_SIZE", "20"))
    require_first_contact_approval: bool = os.getenv(
        "REQUIRE_FIRST_CONTACT_APPROVAL", "true"
    ).lower() in {"1", "true", "yes", "on"}


settings = Settings()
