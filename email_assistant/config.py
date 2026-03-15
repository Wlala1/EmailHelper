import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# Microsoft Azure / Outlook
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET", "")
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID", "")

# Redis / Celery
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Google Sheets
GOOGLE_SHEETS_CREDENTIALS = os.getenv("GOOGLE_SHEETS_CREDENTIALS", str(BASE_DIR / "credentials.json"))
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "EmailTasks")

# Paths
DATA_DIR = BASE_DIR / "data"
ATTACHMENTS_DIR = DATA_DIR / "attachments"
LOGS_DIR = BASE_DIR / "logs"
PREFERENCES_FILE = DATA_DIR / "user_preferences.json"
TOKEN_FILE = BASE_DIR / "o365_token.txt"

# Agent settings
MAX_REACT_ITERATIONS = 10
EMAIL_FETCH_LIMIT = int(os.getenv("EMAIL_FETCH_LIMIT", "10"))
