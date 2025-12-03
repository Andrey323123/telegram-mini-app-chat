import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    BOT_USERNAME = os.getenv("BOT_USERNAME", "")
    PORT = int(os.getenv("PORT", 8000))
    HOST = os.getenv("HOST", "0.0.0.0")
    
    ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
    
    if ENVIRONMENT == "production":
        SQLITE_PATH = "/data/chat.db"
        MEDIA_PATH = "/data/media"
        WS_URL = os.getenv("WS_URL", "ws://localhost:3001")
    else:
        SQLITE_PATH = str(BASE_DIR / "data" / "chat.db")
        MEDIA_PATH = str(BASE_DIR / "data" / "media")
        WS_URL = "ws://localhost:3001"
    
    DATABASE_URL = f"sqlite:///{SQLITE_PATH}"
    
    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
    MAX_DB_SIZE = 100 * 1024 * 1024  # 100MB
    MEDIA_RETENTION_DAYS = 30