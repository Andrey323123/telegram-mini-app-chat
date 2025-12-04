#!/usr/bin/env python3
"""
Telegram Chat Mini App - Полный рабочий код для Railway
"""

import os
import sys
import json
import sqlite3
import logging
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from contextlib import asynccontextmanager
import asyncio

# ======================= НАСТРОЙКА =======================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Определяем путь к данным
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MEDIA_DIR = DATA_DIR / "media"

# Создаем папки если нет
DATA_DIR.mkdir(exist_ok=True)
MEDIA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "chat.db"

# Проверяем режим
IS_RAILWAY = os.getenv("RAILWAY_ENVIRONMENT") == "production"
logger.info(f"🚀 Режим: {'RAILWAY 🏢' if IS_RAILWAY else 'Локальный 💻'}")

# ======================= WEBSOCKET МЕНЕДЖЕР =======================
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, Dict[int, WebSocket]] = {}
    
    async def connect(self, websocket: WebSocket, user_id: int):
        await websocket.accept()
        
        if 1 not in self.active_connections:
            self.active_connections[1] = {}
        
        # Если уже есть соединение - закрываем
        if user_id in self.active_connections[1]:
            try:
                await self.active_connections[1][user_id].close()
            except:
                pass
        
        self.active_connections[1][user_id] = websocket
        logger.info(f"👤 Пользователь {user_id} подключен")
        
        # Уведомляем всех о новом онлайн
        await self.broadcast(1, {
            "type": "user_online",
            "user_id": user_id,
            "online_count": len(self.active_connections[1])
        }, exclude_user=user_id)
    
    def disconnect(self, user_id: int):
        if 1 in self.active_connections and user_id in self.active_connections[1]:
            del self.active_connections[1][user_id]
            logger.info(f"👤 Пользователь {user_id} отключен")
            
            if not self.active_connections[1]:
                del self.active_connections[1]
    
    async def send_to_user(self, user_id: int, message: dict):
        """Отправить сообщение конкретному пользователю"""
        if 1 in self.active_connections and user_id in self.active_connections[1]:
            try:
                await self.active_connections[1][user_id].send_json(message)
                return True
            except Exception as e:
                logger.error(f"Ошибка отправки пользователю {user_id}: {e}")
                self.disconnect(user_id)
        return False
    
    async def broadcast(self, chat_id: int, message: dict, exclude_user: int = None):
        """Отправить всем в чате"""
        if chat_id in self.active_connections:
            for uid, connection in self.active_connections[chat_id].items():
                if uid != exclude_user:
                    try:
                        await connection.send_json(message)
                    except Exception as e:
                        logger.error(f"Ошибка broadcast пользователю {uid}: {e}")
                        self.disconnect(uid)

manager = ConnectionManager()

# ======================= БАЗА ДАННЫХ =======================
def init_db():
    """Инициализация базы данных"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Пользователи
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            avatar_url TEXT,
            is_admin BOOLEAN DEFAULT 0,
            is_banned BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # Сообщения
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            content TEXT,
            media_filename TEXT,
            media_size INTEGER,
            message_type TEXT DEFAULT 'text',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        ''')
        
        # Добавляем тестового пользователя для разработки
        if not IS_RAILWAY:
            cursor.execute("SELECT id FROM users WHERE telegram_id = 123456789")
            if not cursor.fetchone():
                cursor.execute('''
                INSERT INTO users (telegram_id, username, first_name, is_admin)
                VALUES (123456789, 'test_user', 'Тестовый Пользователь', 1)
                ''')
                logger.info("✅ Создан тестовый пользователь")
        
        conn.commit()
        conn.close()
        logger.info(f"✅ База данных инициализирована: {DB_PATH}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        raise

# ======================= LIFESPAN =======================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """События запуска и остановки"""
    # Startup
    logger.info("=" * 50)
    logger.info("🚀 ЗАПУСК TELEGRAM CHAT MINI APP")
    logger.info("=" * 50)
    
    init_db()
    
    # Создаем симлинк для статики если нужно
    static_path = BASE_DIR / "client"
    if static_path.exists():
        logger.info(f"📁 Статика: {static_path}")
    
    if MEDIA_DIR.exists():
        logger.info(f"📁 Медиа: {MEDIA_DIR}")
    
    logger.info(f"🌐 WebSocket: ws://HOST:PORT/ws/{{user_id}}")
    logger.info(f"📊 API: /api/health")
    
    yield
    
    # Shutdown
    logger.info("👋 Остановка приложения...")

# ======================= FASTAPI APP =======================
app = FastAPI(
    title="Telegram Chat Mini App",
    description="Чат для Telegram Mini Apps",
    version="2.0.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Статические файлы
static_dir = BASE_DIR / "client"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    logger.info("✅ Статика подключена: /static")
else:
    logger.warning("⚠️  Папка client/ не найдена")

if MEDIA_DIR.exists():
    app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")
    logger.info("✅ Медиа подключено: /media")

# ======================= API ЭНДПОЙНТЫ =======================
@app.get("/")
async def root():
    """Главная страница"""
    index_path = BASE_DIR / "client" / "index.html"
    
    if index_path.exists():
        with open(index_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        
        # Заменяем localhost на реальный хост
        if IS_RAILWAY:
            html_content = html_content.replace(
                "localhost:8000",
                os.getenv("RAILWAY_STATIC_URL", "").rstrip("/")
            )
        
        return HTMLResponse(html_content)
    
    # Минимальный интерфейс если нет HTML
    return HTMLResponse("""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Telegram Chat Mini App</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: -apple-system, BlinkMacSystemFont, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                min-height: 100vh;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                padding: 20px;
                text-align: center;
            }
            .container {
                background: rgba(255, 255, 255, 0.1);
                backdrop-filter: blur(10px);
                border-radius: 20px;
                padding: 40px;
                max-width: 600px;
                width: 100%;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            }
            h1 {
                font-size: 2.5em;
                margin-bottom: 20px;
                color: white;
            }
            .status {
                background: rgba(255,255,255,0.2);
                border-radius: 12px;
                padding: 20px;
                margin: 20px 0;
            }
            .success { color: #4ade80; }
            .warning { color: #fbbf24; }
            .btn {
                background: white;
                color: #667eea;
                border: none;
                padding: 12px 30px;
                border-radius: 50px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                margin: 10px;
                transition: all 0.3s;
            }
            .btn:hover {
                transform: translateY(-2px);
                box-shadow: 0 10px 25px rgba(0,0,0,0.2);
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>💬 Telegram Chat Mini App</h1>
            <p>Сервер успешно запущен на Railway! 🚀</p>
            
            <div class="status">
                <p><strong>Статус:</strong> <span class="success">✅ Активен</span></p>
                <p><strong>Версия:</strong> 2.0.0</p>
                <p><strong>Режим:</strong> """ + ("Production 🏢" if IS_RAILWAY else "Development 💻") + """</p>
                <p><strong>Онлайн:</strong> """ + str(sum(len(users) for users in manager.active_connections.values())) + """ 👤</p>
            </div>
            
            <div style="margin-top: 30px;">
                <a href="/api/health" class="btn">Проверить здоровье</a>
                <a href="/api/chat/messages" class="btn">Сообщения API</a>
            </div>
            
            <p style="margin-top: 30px; font-size: 14px; opacity: 0.8;">
                Для использования чата откройте через Telegram бота.
                <br>Добавьте файлы в папку client/ для интерфейса.
            </p>
        </div>
        
        <script>
            // Обновляем онлайн счет
            async function updateOnline() {
                try {
                    const res = await fetch('/api/health');
                    const data = await res.json();
                    const onlineEl = document.querySelector('.status p:nth-child(4)');
                    if (onlineEl) {
                        onlineEl.innerHTML = `<strong>Онлайн:</strong> ${data.online_users || 0} 👤`;
                    }
                } catch(e) {}
            }
            setInterval(updateOnline, 5000);
        </script>
    </body>
    </html>
    """)

@app.get("/api/health")
async def health_check():
    """Проверка состояния сервера"""
    try:
        # Проверяем БД
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        user_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM messages")
        message_count = cursor.fetchone()[0]
        conn.close()
        
        return {
            "status": "healthy",
            "service": "telegram-chat-mini-app",
            "timestamp": datetime.now().isoformat(),
            "version": "2.0.0",
            "database": {
                "status": "connected",
                "users": user_count,
                "messages": message_count
            },
            "online_users": sum(len(users) for users in manager.active_connections.values()),
            "environment": "railway" if IS_RAILWAY else "development",
            "websocket": "active"
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }

@app.post("/api/auth/telegram")
async def auth_telegram(request: Request):
    """Авторизация через Telegram WebApp"""
    try:
        data = await request.json()
        init_data = data.get("init_data", "")
        
        # В режиме разработки используем тестового пользователя
        if not init_data or not IS_RAILWAY:
            telegram_id = 123456789
            user_info = {
                "id": telegram_id,
                "username": "test_user",
                "first_name": "Тестовый",
                "last_name": "Пользователь",
                "photo_url": None,
                "is_bot": False
            }
        else:
            # TODO: Реальная валидация Telegram WebApp
            # Пока используем фиктивные данные
            telegram_id = data.get("user", {}).get("id", 0)
            if not telegram_id:
                raise HTTPException(400, "Неверные данные Telegram")
            
            user_info = data.get("user", {})
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Ищем пользователя
        cursor.execute(
            "SELECT id, username, first_name, avatar_url, is_admin FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        user = cursor.fetchone()
        
        if user:
            # Обновляем last_seen
            cursor.execute(
                "UPDATE users SET last_seen = CURRENT_TIMESTAMP WHERE id = ?",
                (user[0],)
            )
            
            user_data = {
                "id": user[0],
                "telegram_id": telegram_id,
                "username": user[1] or user_info.get("username", ""),
                "first_name": user[2] or user_info.get("first_name", ""),
                "avatar_url": user[3],
                "is_admin": bool(user[4])
            }
        else:
            # Создаем нового пользователя
            cursor.execute(
                """INSERT INTO users 
                (telegram_id, username, first_name, last_name, avatar_url) 
                VALUES (?, ?, ?, ?, ?)""",
                (
                    telegram_id,
                    user_info.get("username", ""),
                    user_info.get("first_name", ""),
                    user_info.get("last_name", ""),
                    user_info.get("photo_url")
                )
            )
            conn.commit()
            
            user_data = {
                "id": cursor.lastrowid,
                "telegram_id": telegram_id,
                "username": user_info.get("username", ""),
                "first_name": user_info.get("first_name", ""),
                "avatar_url": user_info.get("photo_url"),
                "is_admin": False
            }
        
        conn.commit()
        conn.close()
        
        logger.info(f"✅ Авторизация: {user_data['first_name']} (ID: {user_data['id']})")
        
        return {
            "success": True,
            "user": user_data,
            "token": secrets.token_hex(16),
            "server_time": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Ошибка авторизации: {e}")
        raise HTTPException(500, f"Ошибка авторизации: {str(e)}")

@app.get("/api/chat/messages")
async def get_messages(limit: int = 50, offset: int = 0):
    """Получить сообщения чата"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT m.*, u.username, u.first_name, u.avatar_url, u.is_admin
        FROM messages m
        JOIN users u ON m.user_id = u.id
        ORDER BY m.created_at DESC
        LIMIT ? OFFSET ?
        ''', (limit, offset))
        
        rows = cursor.fetchall()
        conn.close()
        
        messages = []
        for row in rows:
            messages.append({
                "id": row["id"],
                "user": {
                    "id": row["user_id"],
                    "username": row["username"],
                    "first_name": row["first_name"],
                    "avatar_url": row["avatar_url"],
                    "is_admin": bool(row["is_admin"])
                },
                "content": row["content"],
                "type": row["message_type"] or "text",
                "media_url": f"/media/{row['media_filename']}" if row["media_filename"] else None,
                "media_size": row["media_size"],
                "created_at": row["created_at"]
            })
        
        # Реверсируем чтобы старые сообщения были первыми
        messages.reverse()
        
        return {
            "success": True,
            "messages": messages,
            "count": len(messages),
            "has_more": len(messages) == limit
        }
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения сообщений: {e}")
        raise HTTPException(500, f"Ошибка загрузки сообщений: {str(e)}")

@app.post("/api/chat/send")
async def send_message(
    user_id: int = Form(...),
    content: str = Form(""),
    file: UploadFile = File(None)
):
    """Отправить сообщение"""
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
            raise HTTPException(404, "Пользователь не найден")
        
        if user[1]:  # is_banned
            conn.close()
            raise HTTPException(403, "Пользователь заблокирован")
        
        # Обрабатываем файл
        media_filename = None
        media_size = 0
        message_type = "text"
        
        if file and file.filename:
            # Ограничение 5MB
            MAX_SIZE = 5 * 1024 * 1024
            
            ext = os.path.splitext(file.filename)[1] or ".bin"
            media_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{user_id}_{secrets.token_hex(4)}{ext}"
            file_path = MEDIA_DIR / media_filename
            
            # Читаем файл
            file_content = await file.read()
            media_size = len(file_content)
            
            if media_size > MAX_SIZE:
                conn.close()
                raise HTTPException(413, "Файл слишком большой (макс. 5MB)")
            
            # Сохраняем
            with open(file_path, "wb") as f:
                f.write(file_content)
            
            # Определяем тип
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
        
        # Сохраняем сообщение
        cursor.execute(
            """INSERT INTO messages 
            (user_id, content, media_filename, media_size, message_type) 
            VALUES (?, ?, ?, ?, ?)""",
            (user_id, content.strip(), media_filename, media_size, message_type)
        )
        
        message_id = cursor.lastrowid
        
        # Получаем данные пользователя
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
            "user": {
                "id": user_id,
                "username": user_data[0] if user_data else "",
                "first_name": user_data[1] if user_data else "",
                "avatar_url": user_data[2] if user_data else ""
            },
            "content": content,
            "type": message_type,
            "media_url": f"/media/{media_filename}" if media_filename else None,
            "media_size": media_size,
            "created_at": datetime.now().isoformat()
        }
        
        # Отправляем через WebSocket
        await manager.broadcast(1, {
            "type": "new_message",
            "message": message
        })
        
        logger.info(f"📨 Сообщение отправлено: ID {message_id} от пользователя {user_id}")
        
        return {
            "success": True,
            "message": message
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Ошибка отправки сообщения: {e}")
        raise HTTPException(500, f"Ошибка отправки: {str(e)}")

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int):
    """WebSocket подключение"""
    await manager.connect(websocket, user_id)
    
    try:
        # Отправляем начальные данные
        await websocket.send_json({
            "type": "connected",
            "user_id": user_id,
            "online_count": len(manager.active_connections.get(1, {})),
            "timestamp": datetime.now().isoformat()
        })
        
        # Принимаем сообщения
        while True:
            try:
                data = await websocket.receive_json(timeout=300)
                
                if data.get("type") == "typing":
                    # Пользователь печатает
                    await manager.broadcast(1, {
                        "type": "user_typing",
                        "user_id": user_id,
                        "timestamp": datetime.now().isoformat()
                    }, exclude_user=user_id)
                
                elif data.get("type") == "ping":
                    # Ответ на пинг
                    await websocket.send_json({
                        "type": "pong",
                        "timestamp": datetime.now().isoformat()
                    })
                
            except asyncio.TimeoutError:
                # Отправляем пинг чтобы проверить соединение
                try:
                    await websocket.send_json({"type": "ping"})
                except:
                    break
                    
    except WebSocketDisconnect:
        logger.info(f"🔌 WebSocket отключен: пользователь {user_id}")
    except Exception as e:
        logger.error(f"❌ WebSocket ошибка: {e}")
    finally:
        manager.disconnect(user_id)

@app.get("/api/users/online")
async def get_online_users():
    """Получить список онлайн пользователей"""
    try:
        online_users = []
        
        if 1 in manager.active_connections:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            for user_id in manager.active_connections[1].keys():
                cursor.execute(
                    "SELECT id, username, first_name, avatar_url FROM users WHERE id = ?",
                    (user_id,)
                )
                user = cursor.fetchone()
                if user:
                    online_users.append({
                        "id": user[0],
                        "username": user[1],
                        "first_name": user[2],
                        "avatar_url": user[3]
                    })
            
            conn.close()
        
        return {
            "success": True,
            "users": online_users,
            "count": len(online_users),
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения онлайн пользователей: {e}")
        raise HTTPException(500, str(e))

# ======================= ЗАПУСК =======================
if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    
    print("=" * 50)
    print("🚀 TELEGRAM CHAT MINI APP")
    print("=" * 50)
    print(f"🌐 Сервер: http://{host}:{port}")
    print(f"📊 Health: http://{host}:{port}/api/health")
    print(f"🔌 WebSocket: ws://{host}:{port}/ws/{{user_id}}")
    print("=" * 50)
    
    uvicorn.run(
        "app:app",  # Важно: строка для импорта
        host=host,
        port=port,
        reload=not IS_RAILWAY,  # Автоперезагрузка только в разработке
        log_level="info"
    )
