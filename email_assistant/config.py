import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
ATTACHMENTS_DIR = DATA_DIR / "attachments"
LOGS_DIR = BASE_DIR / "logs"
TOKEN_FILE = BASE_DIR / "o365_token.txt"

OUMA_SCHEMA_VERSION = os.getenv("OUMA_SCHEMA_VERSION", "ouma.v2")

# App / API
API_TITLE = "OUMA Email Assistant API"
API_VERSION = "2.0.0"
DEFAULT_USER_TIMEZONE = os.getenv("DEFAULT_USER_TIMEZONE", "Asia/Singapore")

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

# Database
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'ouma.db').as_posix()}")
SQL_ECHO = os.getenv("SQL_ECHO", "false").lower() == "true"

# Neo4j
NEO4J_URI = os.getenv("NEO4J_URI", "")
NEO4J_USER = os.getenv("NEO4J_USER", "")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

# Microsoft Azure / Outlook
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET", "")
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID", "")

# Agent settings
MAX_ATTACHMENT_TEXT_LENGTH = int(os.getenv("MAX_ATTACHMENT_TEXT_LENGTH", "200000"))
MAX_CLASSIFIER_CONTEXT_CHARS = int(os.getenv("MAX_CLASSIFIER_CONTEXT_CHARS", "12000"))
MAX_RESPONSE_CONTEXT_CHARS = int(os.getenv("MAX_RESPONSE_CONTEXT_CHARS", "6000"))
