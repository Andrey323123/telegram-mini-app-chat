import os
import sys
from pathlib import Path

# ======================= НАЧАЛО =======================
# Сначала ВСЕ импорты, потом код
# =======================

from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, ForeignKey, func
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, Session
from contextlib import asynccontextmanager
import json
from datetime import datetime, timedelta
import shutil
import asyncio
from typing import Dict, Set, List
import uvicorn

# ======================= КОНФИГ =======================
IS_RAILWAY = os.getenv("RAILWAY_ENVIRONMENT") == "production"

if IS_RAILWAY:
    BASE_DIR = Path("/")
    DATA_DIR = Path("/data")
    print("🚂 Railway Production Mode")
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
    DATA_DIR = BASE_DIR / "data"
    print("💻 Local Development Mode")

DATA_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_PATH = DATA_DIR / "media"
MEDIA_PATH.mkdir(parents=True, exist_ok=True)
SQLITE_PATH = DATA_DIR / "chat.db"

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    BOT_USERNAME = os.getenv("BOT_USERNAME", "")
    PORT = int(os.getenv("PORT", 8080))  # Railway использует 8080!
    HOST = os.getenv("HOST", "0.0.0.0")
    DATABASE_URL = f"sqlite:///{SQLITE_PATH}"
    MEDIA_PATH = MEDIA_PATH
    MAX_FILE_SIZE = 5 * 1024 * 1024
    ENVIRONMENT = "production" if IS_RAILWAY else "development"

# ======================= БАЗА ДАННЫХ =======================
engine = create_engine(
    Config.DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ======================= МОДЕЛИ =======================
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    username = Column(String(100))
    first_name = Column(String(100))
    avatar_url = Column(String(500))
    is_admin = Column(Boolean, default=False)
    last_seen = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    messages = relationship("Message", back_populates="user")

class ChatRoom(Base):
    __tablename__ = "chat_rooms"
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    messages = relationship("Message", back_populates="chat_room")

class Message(Base):
    __tablename__ = "messages"
    
    id = Column(Integer, primary_key=True)
    chat_room_id = Column(Integer, ForeignKey("chat_rooms.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    message_type = Column(String(20), default="text")
    content = Column(Text)
    media_filename = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship("User", back_populates="messages")
    chat_room = relationship("ChatRoom", back_populates="messages")

# ======================= LIFESPAN =======================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        if db.query(ChatRoom).count() == 0:
            chat = ChatRoom(
                id=1,
                name="Общий чат",
                description="Добро пожаловать!"
            )
            db.add(chat)
            db.commit()
            print("✅ База данных создана")
    finally:
        db.close()
    
    print(f"🚀 Сервер запущен на порту {Config.PORT}")
    print(f"🌐 Домен: telegram-mini-app-chat-production.up.railway.app")
    
    yield  # App runs here
    
    # Shutdown
    print("👋 Сервер остановлен")

# ======================= FASTAPI APP =======================
app = FastAPI(
    title="Telegram Chat",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="client"), name="static")
app.mount("/media", StaticFiles(directory=Config.MEDIA_PATH), name="media")

# ======================= WEBSOCKET =======================
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, Dict[str, WebSocket]] = {}
        
    async def connect(self, websocket: WebSocket, chat_id: int, user_id: int):
        await websocket.accept()
        if chat_id not in self.active_connections:
            self.active_connections[chat_id] = {}
        self.active_connections[chat_id][user_id] = websocket
    
    def disconnect(self, chat_id: int, user_id: int):
        if chat_id in self.active_connections and user_id in self.active_connections[chat_id]:
            del self.active_connections[chat_id][user_id]

manager = ConnectionManager()

# ======================= API ENDPOINTS =======================
@app.get("/")
async def root():
    return HTMLResponse("""
    <html>
    <body style="background: #0f0f0f; color: white; text-align: center; padding: 50px;">
        <h1 style="color: #4dabf7;">✅ Telegram Chat работает!</h1>
        <p>Домен: telegram-mini-app-chat-production.up.railway.app</p>
        <p>Открой в Telegram через бота</p>
    </body>
    </html>
    """)

@app.get("/api/health")
async def health():
    return JSONResponse({
        "status": "healthy",
        "service": "telegram-chat",
        "railway": IS_RAILWAY,
        "port": Config.PORT
    })

@app.get("/api/chat/messages")
async def get_messages(db: Session = Depends(get_db)):
    messages = db.query(Message).join(User).filter(
        Message.chat_room_id == 1
    ).order_by(Message.created_at.desc()).limit(50).all()
    
    return {
        "success": True,
        "messages": [
            {
                "id": msg.id,
                "user": {
                    "id": msg.user.id,
                    "first_name": msg.user.first_name,
                    "avatar_url": msg.user.avatar_url
                },
                "content": msg.content,
                "created_at": msg.created_at.isoformat()
            }
            for msg in reversed(messages)
        ]
    }

@app.post("/api/auth/telegram")
async def auth_telegram(data: dict, db: Session = Depends(get_db)):
    user_data = data.get("user", {})
    telegram_id = user_data.get("id")
    
    if not telegram_id:
        raise HTTPException(400, "No user data")
    
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    
    if not user:
        user = User(
            telegram_id=telegram_id,
            username=user_data.get("username"),
            first_name=user_data.get("first_name"),
            avatar_url=user_data.get("photo_url")
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    
    return {
        "success": True,
        "user": {
            "id": user.id,
            "first_name": user.first_name,
            "avatar_url": user.avatar_url
        }
    }

# ======================= ЗАПУСК =======================
if __name__ == "__main__":
    print(f"🔧 Конфигурация:")
    print(f"   Порт: {Config.PORT}")
    print(f"   Хост: {Config.HOST}")
    print(f"   БД: {Config.DATABASE_URL}")
    
    uvicorn.run(
        app,
        host=Config.HOST,
        port=Config.PORT,
        log_level="info"
    )
