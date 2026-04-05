import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
ATTACHMENTS_DIR = DATA_DIR / "attachments"
LOGS_DIR = BASE_DIR / "logs"

OUMA_SCHEMA_VERSION = os.getenv("OUMA_SCHEMA_VERSION", "ouma.v2")

# App / API
API_TITLE = "OUMA Email Assistant API"
API_VERSION = "2.0.0"
DEFAULT_USER_TIMEZONE = os.getenv("DEFAULT_USER_TIMEZONE", "Asia/Singapore")
APP_ROLE = os.getenv("APP_ROLE", "api").lower()
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost")
FRONTEND_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_ATTACHMENT_SUMMARY_MODEL = os.getenv("OPENAI_ATTACHMENT_SUMMARY_MODEL", OPENAI_MODEL)
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_STYLE_PROFILE_MODEL = os.getenv("OPENAI_STYLE_PROFILE_MODEL", OPENAI_MODEL)

# Database
# Local dev defaults to SQLite. Set DATABASE_URL=postgresql+psycopg://... in .env or docker-compose.
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'ouma.db').as_posix()}")
SQL_ECHO = os.getenv("SQL_ECHO", "false").lower() == "true"
AUTO_CREATE_SCHEMA = os.getenv(
    "AUTO_CREATE_SCHEMA",
    "true" if DATABASE_URL.startswith("sqlite") else "false",
).lower() == "true"

# Neo4j
NEO4J_URI = os.getenv("NEO4J_URI", "")
NEO4J_USER = os.getenv("NEO4J_USER", "")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
# Set to "true" in production (Docker). App refuses to start if Neo4j unreachable.
NEO4J_REQUIRED = os.getenv("NEO4J_REQUIRED", "false").lower() == "true"

# Microsoft Azure / Outlook
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET", "")
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID", "common")
AZURE_REDIRECT_URI = os.getenv("AZURE_REDIRECT_URI", "http://localhost:8000/auth/microsoft/callback")
AZURE_SCOPE = os.getenv(
    "AZURE_SCOPE",
    "offline_access openid profile User.Read Mail.Read Mail.ReadWrite Mail.Send Calendars.ReadWrite",
)
MICROSOFT_AUTH_BASE_URL = os.getenv("MICROSOFT_AUTH_BASE_URL", "https://login.microsoftonline.com")
MICROSOFT_GRAPH_BASE_URL = os.getenv("MICROSOFT_GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0")

# Agent settings
MAX_ATTACHMENT_TEXT_LENGTH = int(os.getenv("MAX_ATTACHMENT_TEXT_LENGTH", "200000"))
MAX_ATTACHMENT_CONTEXT_CHARS = int(os.getenv("MAX_ATTACHMENT_CONTEXT_CHARS", "8000"))
MAX_CLASSIFIER_CONTEXT_CHARS = int(os.getenv("MAX_CLASSIFIER_CONTEXT_CHARS", "12000"))
MAX_RESPONSE_CONTEXT_CHARS = int(os.getenv("MAX_RESPONSE_CONTEXT_CHARS", "6000"))
BOOTSTRAP_LOOKBACK_DAYS = int(os.getenv("BOOTSTRAP_LOOKBACK_DAYS", "180"))
BOOTSTRAP_MAX_PROFILE_EMAILS = int(os.getenv("BOOTSTRAP_MAX_PROFILE_EMAILS", "200"))
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
BACKGROUND_LOOP_INTERVAL_SECONDS = int(os.getenv("BACKGROUND_LOOP_INTERVAL_SECONDS", "30"))
PROFILE_REBUILD_INTERVAL_SECONDS = int(os.getenv("PROFILE_REBUILD_INTERVAL_SECONDS", "86400"))   # 24h
CATEGORY_SUGGESTION_INTERVAL_SECONDS = int(os.getenv("CATEGORY_SUGGESTION_INTERVAL_SECONDS", "43200"))  # 12h
LEASE_DURATION_SECONDS = int(os.getenv("LEASE_DURATION_SECONDS", "120"))
ENABLE_BACKGROUND_WORKERS = os.getenv("ENABLE_BACKGROUND_WORKERS", "true").lower() == "true"
AUTO_DRAFT_RELATIONSHIP_THRESHOLD = float(os.getenv("AUTO_DRAFT_RELATIONSHIP_THRESHOLD", "0.8"))
CATEGORY_SUGGESTION_BACKFILL_LIMIT = int(os.getenv("CATEGORY_SUGGESTION_BACKFILL_LIMIT", "100"))

# Feature flags for gradual rollout
# Enable time-decayed relationship weights (Phase B)
USE_DECAYED_WEIGHT = os.getenv("USE_DECAYED_WEIGHT", "false").lower() == "true"
# Enable behavioral preference vector for tone/schedule ranking (Phase E)
USE_PREFERENCE_VECTOR = os.getenv("USE_PREFERENCE_VECTOR", "false").lower() == "true"
