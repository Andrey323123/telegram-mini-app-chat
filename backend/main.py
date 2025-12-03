# backend/main.py - РАБОЧАЯ ВЕРСИЯ ДЛЯ RAILWAY
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
import json
import logging

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s'
)
logger = logging.getLogger(__name__)

# Добавляем пути
sys.path.append(str(Path(__file__).parent))

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi import WebSocket, WebSocketDisconnect
import sqlite3
from typing import Dict, Set
import asyncio
import urllib.parse

# ======================= КОНФИГУРАЦИЯ =======================
IS_RAILWAY = os.getenv("RAILWAY_ENVIRONMENT") == "production"

if IS_RAILWAY:
    BASE_DIR = Path("/")
    DATA_DIR = Path("/data")
    logger.info("🚂 Railway Production Mode")
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
    DATA_DIR = BASE_DIR / "data"
    logger.info("💻 Local Development Mode")

# Создаем папки
DATA_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_PATH = DATA_DIR / "media"
MEDIA_PATH.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "chat.db"

class Config:
    PORT = int(os.getenv("PORT", 8080))
    HOST = os.getenv("HOST", "0.0.0.0")
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    BOT_USERNAME = os.getenv("BOT_USERNAME", "")

# ======================= БАЗА ДАННЫХ =======================
def init_db():
    """Инициализация SQLite базы"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Пользователи
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE NOT NULL,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        avatar_url TEXT,
        is_bot BOOLEAN DEFAULT 0,
        is_admin BOOLEAN DEFAULT 0,
        is_banned BOOLEAN DEFAULT 0,
        muted_until TIMESTAMP,
        last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Чаты
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS chat_rooms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        is_public BOOLEAN DEFAULT 1,
        max_members INTEGER DEFAULT 1000,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Сообщения
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_room_id INTEGER DEFAULT 1,
        user_id INTEGER NOT NULL,
        message_type TEXT DEFAULT 'text',
        content TEXT,
        media_filename TEXT,
        media_size INTEGER,
        mentions TEXT,
        is_edited BOOLEAN DEFAULT 0,
        is_deleted BOOLEAN DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )
    ''')
    
    # Создаем общий чат если его нет
    cursor.execute("SELECT COUNT(*) FROM chat_rooms WHERE id = 1")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO chat_rooms (id, name, description) VALUES (1, 'Общий чат', 'Добро пожаловать!')"
        )
    
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

# Инициализируем БД при старте
init_db()

# ======================= ВАЛИДАЦИЯ Telegram WebApp =======================
def validate_init_data(init_data: str) -> dict | None:
    """Валидация данных от Telegram WebApp"""
    if not init_data:
        return None
    
    try:
        params = dict([x.split('=', 1) for x in init_data.split('&')])
        user_str = params.get('user', '')
        
        if not user_str:
            return None

        user_str = urllib.parse.unquote(user_str)
        user_data = json.loads(user_str)
        user_id = int(user_data["id"])
        
        return {
            "user_id": user_id,
            "username": user_data.get("username"),
            "first_name": user_data.get("first_name"),
            "last_name": user_data.get("last_name"),
            "photo_url": user_data.get("photo_url"),
            "is_bot": user_data.get("is_bot", False)
        }
    except Exception as e:
        logger.error(f"Ошибка валидации initData: {e}")
        return None

# ======================= FASTAPI =======================
app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Статические файлы
app.mount("/static", StaticFiles(directory="client"), name="static")
app.mount("/media", StaticFiles(directory=MEDIA_PATH), name="media")

# ======================= WEBSOCKET МЕНЕДЖЕР =======================
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
        
        logger.info(f"👤 Пользователь {user_id} присоединился к чату {chat_id}")
        
    def disconnect(self, chat_id: int, user_id: int):
        if chat_id in self.active_connections and user_id in self.active_connections[chat_id]:
            del self.active_connections[chat_id][user_id]
            
            if not self.active_connections[chat_id]:
                del self.active_connections[chat_id]
            
            if user_id in self.user_chats:
                self.user_chats[user_id].remove(chat_id)
                if not self.user_chats[user_id]:
                    del self.user_chats[user_id]
            
            logger.info(f"👤 Пользователь {user_id} вышел из чата {chat_id}")

manager = ConnectionManager()

# ======================= API ENDPOINTS =======================
@app.get("/")
async def serve_index():
    """Главная страница"""
    index_path = Path("client/index.html")
    if index_path.exists():
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Telegram Chat</title>
        <style>
            body { background: #1a1a1a; color: white; text-align: center; padding: 50px; }
            h1 { color: #4dabf7; }
            .success { color: #4CAF50; font-weight: bold; }
        </style>
    </head>
    <body>
        <h1>💬 Telegram Chat Mini App</h1>
        <p class="success">✅ Сервер работает!</p>
        <p>Домен: telegram-mini-app-chat-production.up.railway.app</p>
        <p>Откройте в Telegram через бота</p>
    </body>
    </html>
    """)

@app.get("/api/health")
async def health_check():
    """Health check для Railway"""
    return JSONResponse({
        "status": "healthy",
        "service": "telegram-chat",
        "railway": IS_RAILWAY,
        "domain": "telegram-mini-app-chat-production.up.railway.app",
        "timestamp": datetime.now(timezone.utc).isoformat()
    })

@app.post("/api/auth/telegram")
async def auth_telegram(request: Request):
    """Авторизация через Telegram WebApp"""
    init_data = request.headers.get("X-Telegram-WebApp-Init-Data")
    user_info = validate_init_data(init_data)
    
    if not user_info:
        # Для разработки - тестовый пользователь
        user_info = {
            "user_id": 123456789,
            "username": "test_user",
            "first_name": "Тестовый",
            "last_name": "Пользователь",
            "photo_url": None,
            "is_bot": False
        }
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Ищем пользователя
    cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user_info["user_id"],))
    user = cursor.fetchone()
    
    if user:
        # Обновляем last_seen
        cursor.execute(
            "UPDATE users SET last_seen = CURRENT_TIMESTAMP WHERE id = ?",
            (user[0],)
        )
        conn.commit()
        
        user_data = {
            "id": user[0],
            "telegram_id": user[1],
            "username": user[2],
            "first_name": user[3],
            "avatar_url": user[5],
            "is_admin": bool(user[7]),
            "is_banned": bool(user[8])
        }
    else:
        # Создаем нового пользователя
        cursor.execute('''
        INSERT INTO users (telegram_id, username, first_name, last_name, avatar_url, is_bot)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            user_info["user_id"],
            user_info["username"],
            user_info["first_name"],
            user_info["last_name"],
            user_info["photo_url"],
            user_info["is_bot"]
        ))
        conn.commit()
        
        user_data = {
            "id": cursor.lastrowid,
            "telegram_id": user_info["user_id"],
            "username": user_info["username"],
            "first_name": user_info["first_name"],
            "avatar_url": user_info["photo_url"],
            "is_admin": False,
            "is_banned": False
        }
    
    conn.close()
    
    return JSONResponse({
        "success": True,
        "user": user_data
    })

@app.get("/api/chat/messages")
async def get_messages(chat_id: int = 1, limit: int = 50):
    """Получить сообщения чата"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT m.*, u.username, u.first_name, u.avatar_url, u.is_admin
    FROM messages m
    JOIN users u ON m.user_id = u.id
    WHERE m.chat_room_id = ? AND m.is_deleted = 0
    ORDER BY m.created_at DESC
    LIMIT ?
    ''', (chat_id, limit))
    
    rows = cursor.fetchall()
    conn.close()
    
    messages = []
    for row in reversed(rows):  # В правильном порядке
        messages.append({
            "id": row[0],
            "user": {
                "id": row[2],
                "username": row[12],
                "first_name": row[13],
                "avatar_url": row[14],
                "is_admin": bool(row[15])
            },
            "content": row[4],
            "type": row[3],
            "media_url": f"/media/{row[5]}" if row[5] else None,
            "created_at": row[10]
        })
    
    return {
        "success": True,
        "messages": messages
    }

@app.post("/api/chat/send")
async def send_message(request: Request):
    """Отправить сообщение в чат"""
    try:
        data = await request.json()
        user_id = data.get("user_id")
        content = data.get("content", "").strip()
        
        if not user_id or not content:
            raise HTTPException(400, "Missing user_id or content")
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Проверяем пользователя
        cursor.execute("SELECT is_banned FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
        
        if not user:
            conn.close()
            raise HTTPException(404, "User not found")
        
        if user[0]:  # is_banned
            conn.close()
            raise HTTPException(403, "User is banned")
        
        # Сохраняем сообщение
        cursor.execute('''
        INSERT INTO messages (chat_room_id, user_id, content)
        VALUES (1, ?, ?)
        ''', (user_id, content))
        
        conn.commit()
        message_id = cursor.lastrowid
        
        # Получаем данные пользователя
        cursor.execute("SELECT username, first_name, avatar_url FROM users WHERE id = ?", (user_id,))
        user_data = cursor.fetchone()
        
        conn.close()
        
        message = {
            "id": message_id,
            "user": {
                "id": user_id,
                "username": user_data[0],
                "first_name": user_data[1],
                "avatar_url": user_data[2]
            },
            "content": content,
            "type": "text",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Отправляем через WebSocket
        await manager.broadcast_to_chat(1, {
            "type": "new_message",
            "message": message
        })
        
        return JSONResponse({
            "success": True,
            "message": message
        })
        
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения: {e}")
        raise HTTPException(500, f"Failed to send message: {str(e)}")

@app.websocket("/ws/{chat_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, chat_id: int, user_id: int):
    """WebSocket endpoint для реального времени"""
    await manager.connect(websocket, chat_id, user_id)
    
    try:
        while True:
            data = await websocket.receive_json()
            message_type = data.get("type")
            
            if message_type == "typing":
                # Пересылаем статус "печатает"
                await manager.broadcast_to_chat(chat_id, {
                    "type": "user_typing",
                    "user_id": user_id
                }, exclude_user=user_id)
                
    except WebSocketDisconnect:
        manager.disconnect(chat_id, user_id)

# ======================= ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ =======================
async def manager_broadcast_to_chat(chat_id: int, message: dict, exclude_user: int = None):
    """Метод для рассылки сообщений в чат"""
    if chat_id in manager.active_connections:
        for uid, connection in manager.active_connections[chat_id].items():
            if uid != exclude_user:
                try:
                    await connection.send_json(message)
                except:
                    pass

# Привязываем метод к менеджеру
manager.broadcast_to_chat = manager_broadcast_to_chat

# ======================= ЗАПУСК =======================
if __name__ == "__main__":
    import uvicorn
    
    port = Config.PORT
    host = Config.HOST
    
    logger.info(f"🚀 Запуск сервера на {host}:{port}")
    logger.info(f"🌐 Домен: telegram-mini-app-chat-production.up.railway.app")
    logger.info(f"📁 База данных: {DB_PATH}")
    
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        log_level="info"
    )
