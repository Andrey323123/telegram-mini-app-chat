# backend/main.py - ГЛАВНЫЙ ФАЙЛ ДЛЯ TELEGRAM MINI APP CHAT
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
import json
import logging
import sqlite3
import asyncio
import urllib.parse
import hashlib
import hmac
import secrets
import re

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from contextlib import asynccontextmanager

# ======================= НАСТРОЙКА ЛОГИРОВАНИЯ =======================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s'
)
logger = logging.getLogger(__name__)

# ======================= КОНФИГУРАЦИЯ =======================
IS_RAILWAY = os.getenv("RAILWAY_ENVIRONMENT") == "production"

if IS_RAILWAY:
    BASE_DIR = Path("/")
    DATA_DIR = Path("/data")
    logger.info("🚂 Railway Production Mode")
else:
    # Файл находится в backend/, поэтому parent.parent
    BASE_DIR = Path(__file__).resolve().parent.parent
    DATA_DIR = BASE_DIR / "data"
    logger.info("💻 Local Development Mode")

MEDIA_DIR = DATA_DIR / "media"
DB_PATH = DATA_DIR / "chat.db"

# Создаем папки
DATA_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# Telegram Bot Token (для валидации WebApp)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")

# ======================= ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ =======================
def init_db():
    """Инициализация SQLite базы данных"""
    try:
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
            logger.info("✅ Создан общий чат")
        
        # Создаем тестового пользователя для разработки
        if not IS_RAILWAY:
            cursor.execute("SELECT COUNT(*) FROM users WHERE telegram_id = 123456789")
            if cursor.fetchone()[0] == 0:
                cursor.execute('''
                INSERT INTO users (telegram_id, username, first_name, is_admin)
                VALUES (123456789, 'test_user', 'Тестовый', 1)
                ''')
                logger.info("✅ Создан тестовый пользователь")
        
        conn.commit()
        conn.close()
        logger.info(f"✅ База данных инициализирована: {DB_PATH}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        raise

# ======================= ВАЛИДАЦИЯ TELEGRAM WEBAPP =======================
def validate_telegram_webapp(init_data: str):
    """Валидация данных от Telegram WebApp"""
    if not init_data:
        logger.warning("Нет initData от Telegram")
        return None
    
    try:
        # Разбираем query строку
        parsed = urllib.parse.parse_qs(init_data)
        
        # Извлекаем данные пользователя
        if 'user' not in parsed:
            logger.warning("Нет данных пользователя в initData")
            return None
        
        user_json = parsed['user'][0]
        user_data = json.loads(user_json)
        
        telegram_id = int(user_data.get("id", 0))
        if not telegram_id:
            return None
        
        return {
            "id": telegram_id,
            "username": user_data.get("username", ""),
            "first_name": user_data.get("first_name", ""),
            "last_name": user_data.get("last_name", ""),
            "photo_url": user_data.get("photo_url"),
            "is_bot": user_data.get("is_bot", False)
        }
        
    except Exception as e:
        logger.error(f"Ошибка валидации Telegram WebApp: {e}")
        return None

# ======================= WEBSOCKET МЕНЕДЖЕР =======================
class ConnectionManager:
    def __init__(self):
        self.active_connections = {}  # chat_id -> {user_id: WebSocket}
        
    async def connect(self, websocket: WebSocket, chat_id: int, user_id: int):
        await websocket.accept()
        
        if chat_id not in self.active_connections:
            self.active_connections[chat_id] = {}
        
        self.active_connections[chat_id][user_id] = websocket
        logger.info(f"👤 User {user_id} connected to chat {chat_id}")
        
        # Уведомляем чат о подключении
        await self.broadcast_to_chat(chat_id, {
            "type": "user_connected",
            "user_id": user_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "online_count": len(self.active_connections[chat_id])
        }, exclude_user=user_id)
        
    def disconnect(self, chat_id: int, user_id: int):
        if chat_id in self.active_connections and user_id in self.active_connections[chat_id]:
            del self.active_connections[chat_id][user_id]
            logger.info(f"👤 User {user_id} disconnected from chat {chat_id}")
            
            # Уведомляем чат об отключении
            asyncio.create_task(self.broadcast_to_chat(chat_id, {
                "type": "user_disconnected",
                "user_id": user_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "online_count": len(self.active_connections.get(chat_id, {}))
            }))
            
            # Очищаем если нет подключений
            if not self.active_connections[chat_id]:
                del self.active_connections[chat_id]
                    
    async def broadcast_to_chat(self, chat_id: int, message: dict, exclude_user: int = None):
        """Отправка сообщения всем в чате"""
        if chat_id not in self.active_connections:
            return
            
        for user_id, connection in self.active_connections[chat_id].items():
            if user_id == exclude_user:
                continue
                
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Ошибка отправки WebSocket: {e}")
                self.disconnect(chat_id, user_id)
                
    async def send_to_user(self, chat_id: int, user_id: int, message: dict):
        """Отправка сообщения конкретному пользователю"""
        if chat_id in self.active_connections and user_id in self.active_connections[chat_id]:
            try:
                await self.active_connections[chat_id][user_id].send_json(message)
            except Exception as e:
                logger.error(f"Ошибка отправки пользователю: {e}")

# Инициализируем менеджер соединений
manager = ConnectionManager()

# ======================= ИНИЦИАЛИЗАЦИЯ FASTAPI =======================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """События жизненного цикла приложения"""
    # Startup
    logger.info("🚀 Starting Telegram Chat Mini App...")
    init_db()
    
    # Проверяем доступность папок
    logger.info(f"📁 Data directory: {DATA_DIR}")
    logger.info(f"📁 Media directory: {MEDIA_DIR}")
    logger.info(f"📊 Database: {DB_PATH}")
    
    yield
    
    # Shutdown
    logger.info("👋 Shutting down...")

app = FastAPI(
    title="Telegram Chat Mini App",
    description="Чат для Telegram Mini App",
    version="1.0.0",
    lifespan=lifespan
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Статические файлы
if (BASE_DIR / "client").exists():
    app.mount("/static", StaticFiles(directory=BASE_DIR / "client"), name="static")
    logger.info("✅ Static files mounted at /static")
    
if MEDIA_DIR.exists():
    app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")
    logger.info("✅ Media files mounted at /media")

# ======================= API ЭНДПОЙНТЫ =======================
@app.get("/")
async def root():
    """Главная страница"""
    index_path = BASE_DIR / "client" / "index.html"
    
    if index_path.exists():
        with open(index_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        
        return HTMLResponse(html_content)
    
    # Минимальный HTML если нет клиентского файла
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Telegram Chat Mini App</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <style>
            body {
                background: #1a1a1a;
                color: white;
                font-family: -apple-system, BlinkMacSystemFont, sans-serif;
                margin: 0;
                padding: 20px;
                text-align: center;
            }
            .container {
                max-width: 600px;
                margin: 0 auto;
                padding: 40px 20px;
            }
            h1 {
                color: #4dabf7;
                margin-bottom: 20px;
            }
            .status {
                background: #2d2d2d;
                border-radius: 12px;
                padding: 20px;
                margin: 20px 0;
            }
            .success {
                color: #4CAF50;
                font-weight: bold;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>💬 Telegram Chat Mini App</h1>
            <p class="success">✅ Сервер работает!</p>
            <div class="status">
                <p><strong>Статус:</strong> <span class="success">Активен</span></p>
                <p><strong>Версия:</strong> 1.0.0</p>
                <p><strong>База данных:</strong> SQLite</p>
                <p><strong>WebSocket:</strong> Включен</p>
            </div>
            <p>Откройте это приложение через Telegram бота для использования чата.</p>
            <p><a href="/api/health">Health Check</a> | <a href="/api/chat/messages">Messages API</a></p>
        </div>
    </body>
    </html>
    """)

@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    db_status = "connected" if os.path.exists(DB_PATH) else "disconnected"
    media_status = "exists" if os.path.exists(MEDIA_DIR) else "missing"
    
    return {
        "status": "healthy",
        "service": "telegram-chat-mini-app",
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database": db_status,
        "media_directory": media_status,
        "active_connections": sum(len(users) for users in manager.active_connections.values()),
        "environment": "production" if IS_RAILWAY else "development"
    }

@app.post("/api/auth/telegram")
async def auth_telegram(request: Request):
    """Авторизация через Telegram WebApp"""
    try:
        data = await request.json()
        init_data = data.get("init_data", "")
        
        # Валидация данных Telegram
        user_info = validate_telegram_webapp(init_data)
        
        # Режим разработки если нет данных Telegram
        if not user_info and not IS_RAILWAY:
            user_info = {
                "id": 123456789,
                "username": "test_user",
                "first_name": "Тестовый",
                "last_name": "Пользователь",
                "photo_url": None,
                "is_bot": False
            }
        elif not user_info:
            raise HTTPException(status_code=401, detail="Invalid Telegram authentication")
        
        # Подключаемся к базе данных
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Ищем пользователя
        cursor.execute(
            "SELECT id, username, first_name, avatar_url, is_admin, is_banned FROM users WHERE telegram_id = ?",
            (user_info["id"],)
        )
        user_row = cursor.fetchone()
        
        if user_row:
            # Обновляем last_seen
            cursor.execute(
                "UPDATE users SET last_seen = CURRENT_TIMESTAMP WHERE id = ?",
                (user_row[0],)
            )
            conn.commit()
            
            user_data = {
                "id": user_row[0],
                "telegram_id": user_info["id"],
                "username": user_row[1] or user_info.get("username", ""),
                "first_name": user_row[2] or user_info.get("first_name", ""),
                "avatar_url": user_row[3],
                "is_admin": bool(user_row[4]),
                "is_banned": bool(user_row[5])
            }
        else:
            # Создаем нового пользователя
            cursor.execute('''
            INSERT INTO users (telegram_id, username, first_name, last_name, avatar_url, is_bot)
            VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                user_info["id"],
                user_info.get("username", ""),
                user_info.get("first_name", ""),
                user_info.get("last_name", ""),
                user_info.get("photo_url"),
                user_info.get("is_bot", False)
            ))
            conn.commit()
            
            user_data = {
                "id": cursor.lastrowid,
                "telegram_id": user_info["id"],
                "username": user_info.get("username", ""),
                "first_name": user_info.get("first_name", ""),
                "avatar_url": user_info.get("photo_url"),
                "is_admin": False,
                "is_banned": False
            }
        
        conn.close()
        
        logger.info(f"✅ User authenticated: {user_data['first_name']} (ID: {user_data['id']})")
        
        return JSONResponse({
            "success": True,
            "user": user_data,
            "token": secrets.token_hex(16),
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        logger.error(f"❌ Auth error: {e}")
        raise HTTPException(status_code=500, detail=f"Authentication failed: {str(e)}")

@app.get("/api/chat/messages")
async def get_messages(
    chat_id: int = Query(1, description="ID чата"),
    limit: int = Query(50, description="Количество сообщений")
):
    """Получение сообщений чата"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT 
            m.id, m.chat_room_id, m.user_id, m.message_type, m.content,
            m.media_filename, m.media_size, m.mentions, m.created_at,
            u.username, u.first_name, u.avatar_url, u.is_admin
        FROM messages m
        JOIN users u ON m.user_id = u.id
        WHERE m.chat_room_id = ? AND m.is_deleted = 0
        ORDER BY m.created_at DESC
        LIMIT ?
        ''', (chat_id, limit))
        
        rows = cursor.fetchall()
        conn.close()
        
        messages = []
        for row in rows:
            # Обрабатываем mentions если есть
            mentions = []
            if row["mentions"]:
                try:
                    mentions = json.loads(row["mentions"])
                except:
                    mentions = []
            
            messages.append({
                "id": row["id"],
                "chat_id": row["chat_room_id"],
                "user": {
                    "id": row["user_id"],
                    "username": row["username"],
                    "first_name": row["first_name"],
                    "avatar_url": row["avatar_url"],
                    "is_admin": bool(row["is_admin"])
                },
                "type": row["message_type"],
                "content": row["content"],
                "media_url": f"/media/{row['media_filename']}" if row["media_filename"] else None,
                "media_size": row["media_size"],
                "mentions": mentions,
                "created_at": row["created_at"]
            })
        
        # Реверсируем чтобы старые сообщения были первыми
        messages.reverse()
        
        return {
            "success": True,
            "messages": messages,
            "count": len(messages),
            "chat_id": chat_id
        }
        
    except Exception as e:
        logger.error(f"❌ Error getting messages: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get messages: {str(e)}")

@app.post("/api/chat/send")
async def send_message(
    user_id: int = Form(...),
    content: str = Form(""),
    file: UploadFile = File(None)
):
    """Отправка сообщения"""
    try:
        # Проверяем пользователя
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT id, is_banned FROM users WHERE id = ?",
            (user_id,)
        )
        user = cursor.fetchone()
        
        if not user:
            conn.close()
            raise HTTPException(status_code=404, detail="User not found")
        
        # Проверяем бан
        if user[1]:  # is_banned
            conn.close()
            raise HTTPException(status_code=403, detail="User is banned")
        
        # Обработка файла если есть
        message_type = "text"
        media_filename = None
        media_size = 0
        
        if file and file.filename:
            # Проверяем размер файла (макс 5MB)
            MAX_SIZE = 5 * 1024 * 1024
            
            # Генерируем уникальное имя файла
            file_extension = os.path.splitext(file.filename)[1] or ".bin"
            unique_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{user_id}{file_extension}"
            file_path = MEDIA_DIR / unique_filename
            
            # Сохраняем файл
            content_bytes = await file.read()
            
            if len(content_bytes) > MAX_SIZE:
                conn.close()
                raise HTTPException(status_code=413, detail="File too large (max 5MB)")
            
            with open(file_path, "wb") as f:
                f.write(content_bytes)
            
            media_filename = unique_filename
            media_size = len(content_bytes)
            
            # Определяем тип сообщения
            if file.content_type:
                if file.content_type.startswith("image/"):
                    message_type = "photo"
                elif file.content_type.startswith("video/"):
                    message_type = "video"
                elif file.content_type.startswith("audio/"):
                    message_type = "voice"
                else:
                    message_type = "file"
            else:
                message_type = "file"
        
        # Сохраняем сообщение в БД
        cursor.execute('''
        INSERT INTO messages (chat_room_id, user_id, message_type, content, media_filename, media_size, created_at)
        VALUES (1, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (
            user_id,
            message_type,
            content.strip() if content else None,
            media_filename,
            media_size
        ))
        
        message_id = cursor.lastrowid
        
        # Получаем данные пользователя для ответа
        cursor.execute(
            "SELECT username, first_name, avatar_url FROM users WHERE id = ?",
            (user_id,)
        )
        user_data = cursor.fetchone()
        
        conn.commit()
        conn.close()
        
        # Формируем объект сообщения
        message = {
            "id": message_id,
            "chat_id": 1,
            "user": {
                "id": user_id,
                "username": user_data[0],
                "first_name": user_data[1],
                "avatar_url": user_data[2]
            },
            "type": message_type,
            "content": content,
            "media_url": f"/media/{media_filename}" if media_filename else None,
            "media_size": media_size,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Отправляем через WebSocket
        await manager.broadcast_to_chat(1, {
            "type": "new_message",
            "message": message
        })
        
        logger.info(f"📨 Message sent: {message_id} by user {user_id}")
        
        return JSONResponse({
            "success": True,
            "message": message
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error sending message: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to send message: {str(e)}")

@app.websocket("/ws/{chat_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, chat_id: int, user_id: int):
    """WebSocket endpoint для реального времени"""
    await manager.connect(websocket, chat_id, user_id)
    
    try:
        while True:
            # Получаем данные от клиента
            data = await websocket.receive_json()
            event_type = data.get("type")
            
            if event_type == "typing":
                # Пользователь печатает
                await manager.broadcast_to_chat(chat_id, {
                    "type": "user_typing",
                    "user_id": user_id,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }, exclude_user=user_id)
                
            elif event_type == "ping":
                # Пинг для поддержания соединения
                await websocket.send_json({
                    "type": "pong",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
                
    except WebSocketDisconnect:
        manager.disconnect(chat_id, user_id)
    except Exception as e:
        logger.error(f"❌ WebSocket error: {e}")
        manager.disconnect(chat_id, user_id)

# ======================= ЗАПУСК СЕРВЕРА =======================
if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    
    logger.info(f"🚀 Starting server on {host}:{port}")
    logger.info(f"🌐 WebSocket: ws://{host}:{port}/ws/{{chat_id}}/{{user_id}}")
    logger.info(f"📊 Health: http://{host}:{port}/api/health")
    
    # Запускаем приложение
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info"
    )
