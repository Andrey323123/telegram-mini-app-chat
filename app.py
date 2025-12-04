# app.py - ВСЁ В ОДНОМ ФАЙЛЕ для Railway
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from contextlib import asynccontextmanager
import os
import json
import sqlite3
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import logging
from typing import Dict, Set
import secrets

# ======================= НАСТРОЙКА =======================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MEDIA_DIR = DATA_DIR / "media"

DATA_DIR.mkdir(exist_ok=True)
MEDIA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "chat.db"
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
IS_PRODUCTION = os.getenv("RAILWAY_ENVIRONMENT") == "production"

# ======================= WEBSOCKET МЕНЕДЖЕР =======================
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, Dict[int, WebSocket]] = {}
    
    async def connect(self, websocket: WebSocket, chat_id: int, user_id: int):
        await websocket.accept()
        
        if chat_id not in self.active_connections:
            self.active_connections[chat_id] = {}
        
        # Удаляем старое соединение если есть
        if user_id in self.active_connections[chat_id]:
            try:
                await self.active_connections[chat_id][user_id].close()
            except:
                pass
        
        self.active_connections[chat_id][user_id] = websocket
        logger.info(f"👤 User {user_id} connected to chat {chat_id}")
    
    def disconnect(self, chat_id: int, user_id: int):
        if chat_id in self.active_connections and user_id in self.active_connections[chat_id]:
            del self.active_connections[chat_id][user_id]
            logger.info(f"👤 User {user_id} disconnected")
            
            if not self.active_connections[chat_id]:
                del self.active_connections[chat_id]
    
    async def send_personal_message(self, message: dict, websocket: WebSocket):
        await websocket.send_json(message)
    
    async def broadcast(self, chat_id: int, message: dict, exclude_user: int = None):
        if chat_id in self.active_connections:
            for user_id, connection in self.active_connections[chat_id].items():
                if user_id != exclude_user:
                    try:
                        await connection.send_json(message)
                    except:
                        self.disconnect(chat_id, user_id)

manager = ConnectionManager()

# ======================= ИНИЦИАЛИЗАЦИЯ БД =======================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        avatar_url TEXT,
        is_admin BOOLEAN DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        content TEXT,
        media_url TEXT,
        message_type TEXT DEFAULT 'text',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("✅ Database initialized")

# ======================= FASTAPI APP =======================
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Starting Telegram Chat App...")
    init_db()
    
    if IS_PRODUCTION:
        logger.info("🏢 Production mode")
    else:
        logger.info("💻 Development mode")
    
    yield
    
    logger.info("👋 Shutting down...")

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Статические файлы
app.mount("/static", StaticFiles(directory="client"), name="static")
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")

# ======================= API ЭНДПОЙНТЫ =======================
@app.get("/")
async def root():
    index_path = BASE_DIR / "client" / "index.html"
    if index_path.exists():
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head><title>Telegram Chat</title></head>
    <body><h1>Telegram Chat Mini App</h1><p>Add index.html to client/</p></body>
    </html>
    """)

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "service": "telegram-chat",
        "timestamp": datetime.now().isoformat(),
        "online_users": sum(len(users) for users in manager.active_connections.values())
    }

@app.post("/api/auth/telegram")
async def auth_telegram(request: Request):
    try:
        data = await request.json()
        user_data = data.get("user", {})
        
        # Для разработки
        if not user_data:
            user_data = {
                "id": 123456789,
                "username": "test_user",
                "first_name": "Тестовый",
                "photo_url": None
            }
        
        telegram_id = user_data.get("id")
        if not telegram_id:
            raise HTTPException(400, "No user ID")
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT id, username, first_name FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        user = cursor.fetchone()
        
        if user:
            user_response = {
                "id": user[0],
                "telegram_id": telegram_id,
                "username": user[1],
                "first_name": user[2],
                "is_admin": True
            }
        else:
            cursor.execute(
                "INSERT INTO users (telegram_id, username, first_name, avatar_url) VALUES (?, ?, ?, ?)",
                (telegram_id, user_data.get("username"), user_data.get("first_name"), user_data.get("photo_url"))
            )
            conn.commit()
            
            user_response = {
                "id": cursor.lastrowid,
                "telegram_id": telegram_id,
                "username": user_data.get("username"),
                "first_name": user_data.get("first_name"),
                "avatar_url": user_data.get("photo_url"),
                "is_admin": False
            }
        
        conn.close()
        return {"success": True, "user": user_response}
        
    except Exception as e:
        logger.error(f"Auth error: {e}")
        raise HTTPException(500, str(e))

@app.get("/api/chat/messages")
async def get_messages(limit: int = 50):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT m.id, m.content, m.media_url, m.message_type, m.created_at,
               u.id, u.username, u.first_name, u.avatar_url
        FROM messages m
        LEFT JOIN users u ON m.user_id = u.id
        ORDER BY m.created_at DESC
        LIMIT ?
        ''', (limit,))
        
        rows = cursor.fetchall()
        conn.close()
        
        messages = []
        for row in reversed(rows):
            messages.append({
                "id": row[0],
                "content": row[1],
                "media_url": row[2],
                "type": row[3],
                "created_at": row[4],
                "user": {
                    "id": row[5],
                    "username": row[6],
                    "first_name": row[7],
                    "avatar_url": row[8]
                }
            })
        
        return {"success": True, "messages": messages}
        
    except Exception as e:
        logger.error(f"Get messages error: {e}")
        raise HTTPException(500, str(e))

@app.post("/api/chat/send")
async def send_message(
    user_id: int = Form(...),
    content: str = Form(""),
    file: UploadFile = File(None)
):
    try:
        media_filename = None
        message_type = "text"
        
        if file and file.filename:
            ext = os.path.splitext(file.filename)[1] or ".bin"
            media_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{user_id}{ext}"
            file_path = MEDIA_DIR / media_filename
            
            file_content = await file.read()
            with open(file_path, "wb") as f:
                f.write(file_content)
            
            if file.content_type and file.content_type.startswith("image/"):
                message_type = "photo"
            elif file.content_type and file.content_type.startswith("audio/"):
                message_type = "voice"
            else:
                message_type = "file"
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT INTO messages (user_id, content, media_url, message_type) VALUES (?, ?, ?, ?)",
            (user_id, content, media_filename, message_type)
        )
        
        conn.commit()
        message_id = cursor.lastrowid
        
        cursor.execute(
            "SELECT username, first_name, avatar_url FROM users WHERE id = ?",
            (user_id,)
        )
        user_data = cursor.fetchone()
        
        conn.close()
        
        message = {
            "id": message_id,
            "content": content,
            "media_url": f"/media/{media_filename}" if media_filename else None,
            "type": message_type,
            "created_at": datetime.now().isoformat(),
            "user": {
                "id": user_id,
                "username": user_data[0] if user_data else "",
                "first_name": user_data[1] if user_data else "",
                "avatar_url": user_data[2] if user_data else ""
            }
        }
        
        # Отправляем через WebSocket
        await manager.broadcast(1, {
            "type": "new_message",
            "message": message
        })
        
        return {"success": True, "message": message}
        
    except Exception as e:
        logger.error(f"Send error: {e}")
        raise HTTPException(500, str(e))

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int):
    await manager.connect(websocket, 1, user_id)
    
    try:
        while True:
            data = await websocket.receive_json()
            event_type = data.get("type")
            
            if event_type == "typing":
                await manager.broadcast(1, {
                    "type": "user_typing",
                    "user_id": user_id,
                    "timestamp": datetime.now().isoformat()
                }, exclude_user=user_id)
            
            elif event_type == "ping":
                await websocket.send_json({"type": "pong"})
                
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        manager.disconnect(1, user_id)

# ======================= ЗАПУСК =======================
if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=port,
        reload=not IS_PRODUCTION
    )
