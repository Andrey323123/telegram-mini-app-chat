import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
import json
import shutil
import asyncio
from typing import Dict, Set, List

# ============================================
# FIX FOR RAILWAY - Сначала определяем пути
# ============================================

# Определяем где мы - Railway или локально
IS_RAILWAY = os.getenv("RAILWAY_ENVIRONMENT") == "production"

if IS_RAILWAY:
    # В Railway
    BASE_DIR = Path("/")
    DATA_DIR = Path("/data")
    SQLITE_PATH = DATA_DIR / "chat.db"
    MEDIA_PATH = DATA_DIR / "media"
    print("🚂 Railway Production Mode")
else:
    # Локально
    BASE_DIR = Path(__file__).resolve().parent.parent
    DATA_DIR = BASE_DIR / "data"
    SQLITE_PATH = DATA_DIR / "chat.db"
    MEDIA_PATH = DATA_DIR / "media"
    print("💻 Local Development Mode")

# Создаем папки если их нет
DATA_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_PATH.mkdir(parents=True, exist_ok=True)

# Простой Config класс
class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    BOT_USERNAME = os.getenv("BOT_USERNAME", "")
    PORT = int(os.getenv("PORT", 8000))
    HOST = os.getenv("HOST", "0.0.0.0")
    
    SQLITE_PATH = SQLITE_PATH
    MEDIA_PATH = MEDIA_PATH
    DATABASE_URL = f"sqlite:///{SQLITE_PATH}"
    
    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
    MAX_DB_SIZE = 100 * 1024 * 1024  # 100MB
    MEDIA_RETENTION_DAYS = 30
    ENVIRONMENT = "production" if IS_RAILWAY else "development"

# ============================================
# SQLAlchemy ИМПОРТЫ (должны быть ДО моделей)
# ============================================

from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, ForeignKey, func, and_
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, Session

# Создаем движок SQLite
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

# ============================================
# МОДЕЛИ SQLAlchemy
# ============================================

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    username = Column(String(100))
    first_name = Column(String(100))
    last_name = Column(String(100))
    avatar_url = Column(String(500))
    is_bot = Column(Boolean, default=False)
    is_admin = Column(Boolean, default=False)
    is_banned = Column(Boolean, default=False)
    muted_until = Column(DateTime, nullable=True)
    last_seen = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    messages = relationship("Message", back_populates="user")

class ChatRoom(Base):
    __tablename__ = "chat_rooms"
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    is_public = Column(Boolean, default=True)
    max_members = Column(Integer, default=1000)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    messages = relationship("Message", back_populates="chat_room")

class Message(Base):
    __tablename__ = "messages"
    
    id = Column(Integer, primary_key=True)
    chat_room_id = Column(Integer, ForeignKey("chat_rooms.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    message_type = Column(String(20), default="text")  # text, photo, voice, file
    content = Column(Text)
    media_filename = Column(String(500))
    media_size = Column(Integer)
    mentions = Column(Text)  # JSON: [{"user_id": 1, "username": "test"}]
    reply_to_id = Column(Integer, ForeignKey("messages.id"), nullable=True)
    is_edited = Column(Boolean, default=False)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)
    
    user = relationship("User", back_populates="messages")
    chat_room = relationship("ChatRoom", back_populates="messages")
    reply_to = relationship("Message", remote_side=[id], backref="replies")

# ============================================
# FASTAPI ИМПОРТЫ
# ============================================

from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse

# ============================================
# FASTAPI ПРИЛОЖЕНИЕ
# ============================================

app = FastAPI(
    title="Telegram Chat Mini App",
    version="1.0",
    docs_url="/docs" if Config.ENVIRONMENT == "development" else None
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Статика
app.mount("/static", StaticFiles(directory="client"), name="static")
app.mount("/media", StaticFiles(directory=Config.MEDIA_PATH), name="media")

# ============================================
# WEBSOCKET МЕНЕДЖЕР
# ============================================

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, Dict[str, WebSocket]] = {}
        self.user_chats: Dict[int, Set[int]] = {}
        
    async def connect(self, websocket: WebSocket, chat_id: int, user_id: int):
        await websocket.accept()
        
        if chat_id not in self.active_connections:
            self.active_connections[chat_id] = {}
        
        self.active_connections[chat_id][user_id] = websocket
        
        if user_id not in self.user_chats:
            self.user_chats[user_id] = set()
        self.user_chats[user_id].add(chat_id)
        
        await self.broadcast_to_chat(chat_id, {
            "type": "user_joined",
            "user_id": user_id,
            "timestamp": datetime.utcnow().isoformat(),
            "online_count": len(self.active_connections[chat_id])
        }, exclude_user=user_id)
        
        await websocket.send_json({
            "type": "online_users",
            "users": list(self.active_connections[chat_id].keys()),
            "count": len(self.active_connections[chat_id])
        })
    
    def disconnect(self, chat_id: int, user_id: int):
        if chat_id in self.active_connections and user_id in self.active_connections[chat_id]:
            del self.active_connections[chat_id][user_id]
            
            if not self.active_connections[chat_id]:
                del self.active_connections[chat_id]
            
            if user_id in self.user_chats:
                self.user_chats[user_id].remove(chat_id)
                if not self.user_chats[user_id]:
                    del self.user_chats[user_id]
            
            asyncio.create_task(self.broadcast_to_chat(chat_id, {
                "type": "user_left",
                "user_id": user_id,
                "timestamp": datetime.utcnow().isoformat(),
                "online_count": len(self.active_connections.get(chat_id, {}))
            }))
    
    async def broadcast_to_chat(self, chat_id: int, message: dict, exclude_user: int = None):
        if chat_id in self.active_connections:
            for user_id, connection in self.active_connections[chat_id].items():
                if user_id != exclude_user:
                    try:
                        await connection.send_json(message)
                    except:
                        pass
    
    async def send_to_user(self, user_id: int, message: dict):
        for chat_id in self.user_chats.get(user_id, []):
            if chat_id in self.active_connections and user_id in self.active_connections[chat_id]:
                try:
                    await self.active_connections[chat_id][user_id].send_json(message)
                except:
                    pass

manager = ConnectionManager()

# ============================================
# СОБЫТИЯ ЗАПУСКА
# ============================================

@app.on_event("startup")
async def startup():
    # Создаем таблицы
    Base.metadata.create_all(bind=engine)
    
    # Создаем общий чат если его нет
    db = SessionLocal()
    try:
        if db.query(ChatRoom).count() == 0:
            general_chat = ChatRoom(
                id=1,
                name="Общий чат",
                description="Добро пожаловать в групповой чат!",
                is_public=True,
                max_members=10000
            )
            db.add(general_chat)
            db.commit()
            print("✅ База данных и общий чат созданы")
    finally:
        db.close()
    
    print(f"🚀 Сервер запущен на порту {Config.PORT}")
    print(f"🌐 Домен: telegram-mini-app-chat-production.up.railway.app")

async def cleanup_old_media_task():
    """Фоновая задача очистки старых медиа"""
    while True:
        await asyncio.sleep(3600)  # Каждый час
        cleanup_old_media()

def cleanup_old_media():
    """Очистка медиа файлов старше 30 дней"""
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=Config.MEDIA_RETENTION_DAYS)
        media_dir = Path(Config.MEDIA_PATH)
        
        if media_dir.exists():
            for file_path in media_dir.rglob("*"):
                if file_path.is_file():
                    file_age = datetime.fromtimestamp(file_path.stat().st_mtime)
                    if file_age < cutoff_date:
                        file_path.unlink()
            print(f"✅ Очистка старых медиа выполнена: {datetime.utcnow()}")
    except Exception as e:
        print(f"⚠️ Ошибка очистки медиа: {e}")

# ============================================
# API ЭНДПОИНТЫ
# ============================================

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    """Отдаем главную страницу"""
    index_path = Path("client/index.html")
    if index_path.exists():
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    
    # Если файла нет - отдаем простую страницу
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Telegram Chat</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: #1a1a1a;
                color: white;
                height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }
            .container {
                max-width: 400px;
                text-align: center;
                background: #212121;
                padding: 30px;
                border-radius: 20px;
                border: 1px solid #333;
            }
            h1 { 
                color: #4dabf7; 
                margin-bottom: 20px;
                font-size: 24px;
            }
            .success { 
                color: #4CAF50; 
                font-weight: bold;
                margin: 10px 0;
            }
            .domain {
                background: #2d2d2d;
                padding: 10px;
                border-radius: 10px;
                margin: 15px 0;
                font-family: monospace;
                word-break: break-all;
            }
            button {
                background: #4dabf7;
                color: white;
                border: none;
                padding: 12px 24px;
                border-radius: 25px;
                font-size: 16px;
                cursor: pointer;
                margin-top: 20px;
                width: 100%;
                transition: background 0.2s;
            }
            button:hover {
                background: #3d8bd6;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>💬 Telegram Chat Mini App</h1>
            <p class="success">✅ Сервер работает на Railway!</p>
            
            <div class="domain">
                telegram-mini-app-chat-production.up.railway.app
            </div>
            
            <p>Откройте приложение через Telegram бота</p>
            
            <button onclick="initTelegram()">
                📱 Проверить Telegram Web App
            </button>
        </div>
        
        <script>
            function initTelegram() {
                if (window.Telegram && window.Telegram.WebApp) {
                    const tg = window.Telegram.WebApp;
                    tg.expand();
                    tg.enableClosingConfirmation();
                    
                    const user = tg.initDataUnsafe.user;
                    if (user) {
                        alert(`Привет, ${user.first_name}! ID: ${user.id}`);
                    } else {
                        alert('Telegram Web App готов! Откройте через бота.');
                    }
                } else {
                    alert('Откройте эту страницу через Telegram бота');
                }
            }
        </script>
    </body>
    </html>
    """)

@app.get("/api/health")
async def health_check(db: Session = Depends(get_db)):
    """Проверка здоровья сервера"""
    try:
        # Проверка базы данных
        db.execute("SELECT 1")
        
        # Статистика
        users_count = db.query(func.count(User.id)).scalar() or 0
        messages_count = db.query(func.count(Message.id)).scalar() or 0
        
        # Размер базы
        db_size = 0
        if os.path.exists(Config.SQLITE_PATH):
            db_size = os.path.getsize(Config.SQLITE_PATH)
        
        return {
            "status": "healthy",
            "environment": Config.ENVIRONMENT,
            "database": "connected",
            "stats": {
                "users": users_count,
                "messages": messages_count,
                "db_size_mb": round(db_size / (1024 * 1024), 2)
            },
            "railway": IS_RAILWAY,
            "domain": "telegram-mini-app-chat-production.up.railway.app",
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "railway": IS_RAILWAY
        }

@app.post("/api/auth/telegram")
async def auth_telegram(data: dict, db: Session = Depends(get_db)):
    """Авторизация через Telegram Web App"""
    try:
        user_data = data.get("user", {})
        telegram_id = user_data.get("id")
        
        if not telegram_id:
            raise HTTPException(status_code=400, detail="No user data provided")
        
        # Ищем или создаем пользователя
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        
        if not user:
            user = User(
                telegram_id=telegram_id,
                username=user_data.get("username"),
                first_name=user_data.get("first_name"),
                last_name=user_data.get("last_name"),
                avatar_url=user_data.get("photo_url"),
                is_bot=user_data.get("is_bot", False)
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        else:
            # Обновляем данные
            user.username = user_data.get("username") or user.username
            user.first_name = user_data.get("first_name") or user.first_name
            user.last_name = user_data.get("last_name") or user.last_name
            user.avatar_url = user_data.get("photo_url") or user.avatar_url
            user.last_seen = datetime.utcnow()
            db.commit()
        
        return JSONResponse({
            "success": True,
            "user": {
                "id": user.id,
                "telegram_id": user.telegram_id,
                "username": user.username,
                "first_name": user.first_name,
                "avatar_url": user.avatar_url,
                "is_admin": user.is_admin
            }
        })
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Authentication error: {str(e)}")

@app.get("/api/chat/messages")
async def get_messages(
    chat_id: int = 1,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Получить сообщения чата"""
    try:
        messages = db.query(Message).join(User).filter(
            Message.chat_room_id == chat_id,
            Message.is_deleted == False
        ).order_by(Message.created_at.desc()).limit(limit).all()
        
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
                    "type": msg.message_type,
                    "media_url": f"/media/{msg.media_filename}" if msg.media_filename else None,
                    "created_at": msg.created_at.isoformat()
                }
                for msg in reversed(messages)
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load messages: {str(e)}")

@app.post("/api/chat/send")
async def send_message(
    user_id: int = Form(...),
    content: str = Form(""),
    file: UploadFile = File(None),
    db: Session = Depends(get_db)
):
    """Отправить сообщение в чат"""
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        message_type = "text"
        media_filename = None
        media_size = 0
        
        if file and file.filename:
            # Проверяем размер
            file_content = await file.read()
            media_size = len(file_content)
            
            if media_size > Config.MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail=f"File too large. Max size: {Config.MAX_FILE_SIZE // (1024*1024)}MB"
                )
            
            # Сохраняем файл
            ext = os.path.splitext(file.filename)[1] or ".bin"
            media_filename = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{user_id}{ext}"
            file_path = Path(Config.MEDIA_PATH) / media_filename
            
            with open(file_path, "wb") as f:
                f.write(file_content)
            
            if file.content_type.startswith("image/"):
                message_type = "photo"
            elif file.content_type.startswith("audio/"):
                message_type = "voice"
            else:
                message_type = "file"
        
        # Сохраняем сообщение
        message = Message(
            chat_room_id=1,
            user_id=user_id,
            message_type=message_type,
            content=content,
            media_filename=media_filename,
            media_size=media_size,
            created_at=datetime.utcnow()
        )
        
        db.add(message)
        db.commit()
        db.refresh(message)
        
        return JSONResponse({
            "success": True,
            "message": {
                "id": message.id,
                "user": {
                    "id": user.id,
                    "first_name": user.first_name,
                    "avatar_url": user.avatar_url
                },
                "content": content,
                "type": message_type,
                "media_url": f"/media/{media_filename}" if media_filename else None,
                "created_at": message.created_at.isoformat()
            }
        })
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send message: {str(e)}")

@app.get("/api/users/online")
async def get_online_users():
    """Получить онлайн пользователей"""
    return {
        "success": True,
        "users": [],
        "count": 0
    }

@app.websocket("/ws/{chat_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, chat_id: int, user_id: int):
    """WebSocket endpoint для реального времени"""
    try:
        await manager.connect(websocket, chat_id, user_id)
        
        # Обновляем last_seen
        db = SessionLocal()
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.last_seen = datetime.utcnow()
            db.commit()
        db.close()
        
        while True:
            data = await websocket.receive_json()
            message_type = data.get("type")
            
            if message_type == "typing":
                await manager.broadcast_to_chat(chat_id, {
                    "type": "user_typing",
                    "user_id": user_id
                }, exclude_user=user_id)
                
    except WebSocketDisconnect:
        manager.disconnect(chat_id, user_id)
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(chat_id, user_id)

# ============================================
# ЗАПУСК СЕРВЕРА
# ============================================

if __name__ == "__main__":
    import uvicorn
    
    if Config.ENVIRONMENT == "development":
        uvicorn.run("main:app", host=Config.HOST, port=Config.PORT, reload=True)
    else:
        uvicorn.run(app, host=Config.HOST, port=Config.PORT)
