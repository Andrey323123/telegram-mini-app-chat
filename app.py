#!/usr/bin/env python3
"""
Telegram Chat Mini App - Полный исправленный код для Railway
"""

import os
import sys
import json
import sqlite3
import logging
import secrets
import socket
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, List
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from contextlib import asynccontextmanager
import asyncio

# ======================= НАСТРОЙКА ЛОГИРОВАНИЯ =======================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(name)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Дополнительные логгеры для отладки
uvicorn_logger = logging.getLogger("uvicorn")
uvicorn_logger.setLevel(logging.INFO)
uvicorn_access_logger = logging.getLogger("uvicorn.access")
uvicorn_access_logger.setLevel(logging.INFO)

# ======================= ДИАГНОСТИКА ОКРУЖЕНИЯ =======================
def diagnose_environment():
    """Диагностика окружения Railway"""
    logger.info("🔍 ДИАГНОСТИКА ОКРУЖЕНИЯ RAILWAY:")
    logger.info("=" * 60)
    
    # Все переменные окружения (без секретов)
    for key, value in os.environ.items():
        if not any(secret in key.lower() for secret in ['token', 'key', 'secret', 'password']):
            logger.info(f"  {key}: {value}")
    
    # Сетевая диагностика
    try:
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
        logger.info(f"  Hostname: {hostname}")
        logger.info(f"  IP: {ip}")
    except:
        pass
    
    # Проверка порта
    port = os.environ.get("PORT", "8000")
    logger.info(f"  PORT из переменных: {port}")
    
    # Проверка Railway окружения
    railway_env = os.environ.get("RAILWAY_ENVIRONMENT", "not set")
    railway_project = os.environ.get("RAILWAY_PROJECT_NAME", "not set")
    railway_service = os.environ.get("RAILWAY_SERVICE_NAME", "not set")
    
    logger.info(f"  RAILWAY_ENVIRONMENT: {railway_env}")
    logger.info(f"  RAILWAY_PROJECT_NAME: {railway_project}")
    logger.info(f"  RAILWAY_SERVICE_NAME: {railway_service}")
    
    # Проверка пути
    logger.info(f"  Текущая директория: {os.getcwd()}")
    logger.info(f"  Файлы в директории: {os.listdir('.')}")
    
    logger.info("=" * 60)

# Выполняем диагностику при старте
diagnose_environment()

# ======================= КОНФИГУРАЦИЯ ПУТЕЙ =======================
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MEDIA_DIR = DATA_DIR / "media"

# Создаем папки если нет
DATA_DIR.mkdir(exist_ok=True)
MEDIA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "chat.db"

# Проверяем режим Railway
IS_RAILWAY = os.getenv("RAILWAY_ENVIRONMENT") == "production"
RAILWAY_PUBLIC_URL = os.getenv("RAILWAY_PUBLIC_URL", "")
RAILWAY_STATIC_URL = os.getenv("RAILWAY_STATIC_URL", "")

logger.info(f"✅ Режим: {'RAILWAY 🚂 ПРОД' if IS_RAILWAY else 'ЛОКАЛЬНЫЙ 💻'}")
if RAILWAY_PUBLIC_URL:
    logger.info(f"🌐 Public URL: {RAILWAY_PUBLIC_URL}")
if RAILWAY_STATIC_URL:
    logger.info(f"📁 Static URL: {RAILWAY_STATIC_URL}")

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
    logger.info("=" * 60)
    logger.info("🚀 ЗАПУСК TELEGRAM CHAT MINI APP НА RAILWAY")
    logger.info("=" * 60)
    
    init_db()
    
    # Проверяем наличие папок
    static_path = BASE_DIR / "client"
    if static_path.exists():
        logger.info(f"📁 Статика найдена: {static_path}")
        logger.info(f"📁 Файлы в static: {list(static_path.iterdir())}")
    else:
        logger.warning(f"⚠️  Папка client/ не найдена: {static_path}")
    
    if MEDIA_DIR.exists():
        logger.info(f"📁 Медиа найдено: {MEDIA_DIR}")
    else:
        logger.info(f"📁 Медиа создано: {MEDIA_DIR}")
    
    logger.info(f"🌐 WebSocket URL: wss://{RAILWAY_PUBLIC_URL}/ws/{{user_id}}" if RAILWAY_PUBLIC_URL else "🌐 WebSocket: ws://localhost:8000/ws/{user_id}")
    logger.info(f"📊 Health Check: {'https://' + RAILWAY_PUBLIC_URL + '/api/health' if RAILWAY_PUBLIC_URL else 'http://localhost:8000/api/health'}")
    
    # Генерируем публичный URL для фронтенда
    if RAILWAY_PUBLIC_URL:
        public_ws_url = f"wss://{RAILWAY_PUBLIC_URL}/ws"
        public_api_url = f"https://{RAILWAY_PUBLIC_URL}"
        logger.info(f"🔗 Публичный WebSocket: {public_ws_url}")
        logger.info(f"🔗 Публичный API: {public_api_url}")
    
    yield
    
    # Shutdown
    logger.info("👋 Остановка приложения...")

# ======================= FASTAPI APP =======================
app = FastAPI(
    title="Telegram Chat Mini App",
    description="Чат для Telegram Mini Apps на Railway",
    version="2.1.0",
    lifespan=lifespan,
    docs_url="/docs" if IS_RAILWAY else "/docs",
    redoc_url="/redoc" if IS_RAILWAY else None,
    openapi_url="/openapi.json" if IS_RAILWAY else "/openapi.json"
)

# ======================= MIDDLEWARE ДЛЯ ЛОГИРОВАНИЯ =======================
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Логирование всех запросов"""
    start_time = time.time()
    
    # Получаем реальный IP (через Railway прокси)
    real_ip = request.headers.get("X-Real-IP", request.client.host)
    cf_connecting_ip = request.headers.get("CF-Connecting-IP")
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    
    logger.info(f"📍 ВХОДЯЩИЙ ЗАПРОС: {request.method} {request.url.path}")
    logger.info(f"   Client IP: {real_ip}")
    logger.info(f"   CF IP: {cf_connecting_ip}")
    logger.info(f"   X-Forwarded-For: {x_forwarded_for}")
    
    try:
        response = await call_next(request)
        process_time = time.time() - start_time
        response.headers["X-Process-Time"] = str(process_time)
        
        logger.info(f"✅ ОТВЕТ: {request.method} {request.url.path} - Status: {response.status_code} - Time: {process_time:.3f}s")
        
        return response
    except Exception as e:
        logger.error(f"❌ ОШИБКА В ЗАПРОСЕ {request.method} {request.url.path}: {e}")
        raise

# CORS - разрешаем всё для Railway
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Разрешаем все origins на Railway
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# Статические файлы
static_dir = BASE_DIR / "client"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    logger.info("✅ Статика подключена: /static")
else:
    logger.warning("⚠️  Папка client/ не найдена, статика не подключена")

if MEDIA_DIR.exists():
    app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")
    logger.info("✅ Медиа подключено: /media")

# ======================= API ЭНДПОЙНТЫ =======================
@app.get("/", response_class=PlainTextResponse)
async def root_simple(request: Request):
    """ПРОСТОЙ корневой эндпоинт для Railway health check"""
    logger.info(f"📍 Запрос к корню от {request.client.host}")
    
    # Получаем публичный URL если есть
    public_url = RAILWAY_PUBLIC_URL or f"http://{request.base_url.hostname}:{request.base_url.port}"
    
    return f"""Telegram Chat Mini App is running on Railway! ✅

Service Information:
• Version: 2.1.0
• Environment: {'PRODUCTION 🚂' if IS_RAILWAY else 'DEVELOPMENT 💻'}
• Public URL: {public_url}
• Timestamp: {datetime.now().isoformat()}
• Online Users: {sum(len(users) for users in manager.active_connections.values())}

API Endpoints:
• Health Check: {public_url}/api/health
• API Docs: {public_url}/docs
• WebSocket: {public_url.replace('http', 'ws')}/ws/{{user_id}}
• Messages API: {public_url}/api/chat/messages

Debug Info:
• Request Host: {request.base_url}
• Client IP: {request.client.host}
• Railway Env: {os.environ.get('RAILWAY_ENVIRONMENT', 'not set')}
"""

@app.get("/home", response_class=HTMLResponse)
async def home(request: Request):
    """HTML интерфейс с автоматическим определением URL"""
    logger.info(f"📍 Запрос к /home от {request.client.host}")
    
    # Определяем базовый URL
    if RAILWAY_PUBLIC_URL:
        base_url = f"https://{RAILWAY_PUBLIC_URL}"
        ws_url = f"wss://{RAILWAY_PUBLIC_URL}/ws"
    else:
        base_url = str(request.base_url).rstrip("/")
        ws_url = f"ws://{request.base_url.hostname}:{request.base_url.port}/ws"
    
    index_path = BASE_DIR / "client" / "index.html"
    
    if index_path.exists():
        with open(index_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        
        # Автоматически заменяем все localhost ссылки
        html_content = html_content.replace("localhost:8000", RAILWAY_PUBLIC_URL or f"{request.base_url.hostname}:{request.base_url.port}")
        html_content = html_content.replace("127.0.0.1:8000", RAILWAY_PUBLIC_URL or f"{request.base_url.hostname}:{request.base_url.port}")
        html_content = html_content.replace("http://localhost", "https://" + RAILWAY_PUBLIC_URL if RAILWAY_PUBLIC_URL else str(request.base_url))
        
        logger.info(f"✅ Отправлен HTML интерфейс. Base URL: {base_url}, WebSocket: {ws_url}")
        
        return HTMLResponse(html_content)
    
    # Fallback HTML если нет файла
    return HTMLResponse(f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Telegram Chat Mini App</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
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
            }}
            .container {{
                background: rgba(255, 255, 255, 0.1);
                backdrop-filter: blur(10px);
                border-radius: 20px;
                padding: 40px;
                max-width: 600px;
                width: 100%;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            }}
            h1 {{ font-size: 2.5em; margin-bottom: 20px; color: white; }}
            .status {{ background: rgba(255,255,255,0.2); border-radius: 12px; padding: 20px; margin: 20px 0; }}
            .success {{ color: #4ade80; }}
            .warning {{ color: #fbbf24; }}
            .btn {{
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
                text-decoration: none;
                display: inline-block;
            }}
            .btn:hover {{
                transform: translateY(-2px);
                box-shadow: 0 10px 25px rgba(0,0,0,0.2);
            }}
            .url-info {{
                background: rgba(0,0,0,0.3);
                border-radius: 10px;
                padding: 15px;
                margin: 15px 0;
                font-family: monospace;
                font-size: 14px;
                word-break: break-all;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>💬 Telegram Chat Mini App</h1>
            <p>Сервер успешно запущен на Railway! 🚀</p>
            
            <div class="status">
                <p><strong>Статус:</strong> <span class="success">✅ Активен</span></p>
                <p><strong>Версия:</strong> 2.1.0</p>
                <p><strong>Режим:</strong> {"Production 🚂" if IS_RAILWAY else "Development 💻"}</p>
                <p><strong>Онлайн:</strong> {sum(len(users) for users in manager.active_connections.values())} 👤</p>
                <p><strong>База URL:</strong> {base_url}</p>
                <p><strong>WebSocket URL:</strong> {ws_url}</p>
            </div>
            
            <div class="url-info">
                <strong>Текущий URL:</strong><br>
                {request.base_url}<br><br>
                <strong>Railway Public URL:</strong><br>
                {RAILWAY_PUBLIC_URL or "Не установлен"}
            </div>
            
            <div style="margin-top: 30px;">
                <a href="/api/health" class="btn">Проверить здоровье</a>
                <a href="/docs" class="btn">API Документация</a>
                <a href="/debug" class="btn">Отладка</a>
            </div>
            
            <p style="margin-top: 30px; font-size: 14px; opacity: 0.8;">
                Для использования чата откройте через Telegram бота.
                <br>Добавьте файлы в папку client/ для интерфейса.
            </p>
        </div>
        
        <script>
            // Автоматическое обновление информации
            async function updateInfo() {{
                try {{
                    const res = await fetch('/api/health');
                    const data = await res.json();
                    const onlineEl = document.querySelector('.status p:nth-child(4)');
                    if (onlineEl) {{
                        onlineEl.innerHTML = `<strong>Онлайн:</strong> ${{data.online_users || 0}} 👤`;
                    }}
                }} catch(e) {{}}
            }}
            setInterval(updateInfo, 5000);
            
            // Тест WebSocket
            function testWebSocket() {{
                const ws = new WebSocket('{ws_url}/123');
                ws.onopen = () => console.log('WebSocket connected!');
                ws.onmessage = (e) => console.log('WebSocket message:', e.data);
                ws.onerror = (e) => console.error('WebSocket error:', e);
            }}
            
            // Авто-тест при загрузке
            window.addEventListener('load', () => {{
                updateInfo();
                // testWebSocket();
            }});
        </script>
    </body>
    </html>
    """)

@app.get("/ping", response_class=PlainTextResponse)
async def ping(request: Request):
    """Простейший ping для Railway health check"""
    logger.info(f"📍 Ping запрос от {request.client.host}")
    return "pong ✅"

@app.get("/debug")
async def debug_info(request: Request):
    """Полная отладочная информация"""
    logger.info(f"📍 Debug запрос от {request.client.host}")
    
    # Получаем все заголовки
    headers = dict(request.headers)
    
    # Собираем информацию о Railway
    railway_info = {}
    for key, value in os.environ.items():
        if key.startswith("RAILWAY_"):
            railway_info[key] = value
    
    # Проверяем доступность папок
    folders = {
        "current": os.getcwd(),
        "base": str(BASE_DIR),
        "data": str(DATA_DIR) if DATA_DIR.exists() else "NOT FOUND",
        "media": str(MEDIA_DIR) if MEDIA_DIR.exists() else "NOT FOUND",
        "client": str(BASE_DIR / "client") if (BASE_DIR / "client").exists() else "NOT FOUND",
        "database": str(DB_PATH) if DB_PATH.exists() else "NOT FOUND"
    }
    
    # Проверяем доступ к базе данных
    db_status = "UNKNOWN"
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        user_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM messages")
        message_count = cursor.fetchone()[0]
        conn.close()
        db_status = f"OK (Users: {user_count}, Messages: {message_count})"
    except Exception as e:
        db_status = f"ERROR: {e}"
    
    return {
        "status": "running",
        "service": "telegram-chat-mini-app",
        "timestamp": datetime.now().isoformat(),
        "request": {
            "method": request.method,
            "url": str(request.url),
            "base_url": str(request.base_url),
            "client": f"{request.client.host}:{request.client.port}" if request.client else "unknown",
            "headers_count": len(headers)
        },
        "environment": {
            "is_railway": IS_RAILWAY,
            "railway_public_url": RAILWAY_PUBLIC_URL,
            "railway_static_url": RAILWAY_STATIC_URL,
            "port": os.environ.get("PORT", "8000"),
            "python_version": sys.version,
            "hostname": socket.gethostname()
        },
        "railway_variables": railway_info,
        "folders": folders,
        "database": {
            "path": str(DB_PATH),
            "status": db_status,
            "exists": DB_PATH.exists()
        },
        "websocket": {
            "active_connections": sum(len(users) for users in manager.active_connections.values()),
            "chats": len(manager.active_connections)
        },
        "endpoints": {
            "root": str(request.base_url),
            "ping": str(request.base_url) + "ping",
            "home": str(request.base_url) + "home",
            "health": str(request.base_url) + "api/health",
            "docs": str(request.base_url) + "docs",
            "debug": str(request.base_url) + "debug",
            "websocket": str(request.base_url).replace("http", "ws") + "ws/{user_id}"
        },
        "headers_sample": {k: v for k, v in list(headers.items())[:10]}  # Первые 10 заголовков
    }

@app.get("/api/health")
async def health_check(request: Request):
    """Расширенная проверка состояния сервера"""
    logger.info(f"📍 Health check от {request.client.host}")
    
    try:
        # Проверяем БД
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        user_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM messages")
        message_count = cursor.fetchone()[0]
        
        # Получаем последние сообщения
        cursor.execute("SELECT created_at FROM messages ORDER BY created_at DESC LIMIT 1")
        last_message = cursor.fetchone()
        last_message_time = last_message[0] if last_message else None
        
        conn.close()
        
        # Собираем метрики
        health_data = {
            "status": "healthy",
            "service": "telegram-chat-mini-app",
            "timestamp": datetime.now().isoformat(),
            "version": "2.1.0",
            "environment": "railway" if IS_RAILWAY else "development",
            "railway": {
                "is_railway": IS_RAILWAY,
                "public_url": RAILWAY_PUBLIC_URL or "not set",
                "static_url": RAILWAY_STATIC_URL or "not set"
            },
            "database": {
                "status": "connected",
                "path": str(DB_PATH),
                "users": user_count,
                "messages": message_count,
                "last_message": last_message_time
            },
            "websocket": {
                "active_connections": sum(len(users) for users in manager.active_connections.values()),
                "active_chats": len(manager.active_connections),
                "status": "active"
            },
            "storage": {
                "data_dir": str(DATA_DIR),
                "media_dir": str(MEDIA_DIR),
                "client_dir": str(BASE_DIR / "client") if (BASE_DIR / "client").exists() else "not found"
            },
            "request_info": {
                "client_ip": request.client.host if request.client else "unknown",
                "request_url": str(request.url),
                "request_method": request.method
            },
            "endpoints": {
                "api_docs": f"{request.base_url}docs",
                "api_health": f"{request.base_url}api/health",
                "api_messages": f"{request.base_url}api/chat/messages",
                "websocket": f"{request.base_url}".replace("http", "ws") + "ws/{user_id}",
                "debug": f"{request.base_url}debug"
            }
        }
        
        logger.info(f"✅ Health check пройден: {health_data['status']}")
        return health_data
        
    except Exception as e:
        logger.error(f"❌ Health check ошибка: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
            "service": "telegram-chat-mini-app"
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
async def get_messages(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
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

# ======================= ЗАПУСК СЕРВЕРА ДЛЯ RAILWAY =======================
def start_server():
    """Запуск сервера с правильной конфигурацией для Railway"""
    import uvicorn
    
    # Railway ВСЕГДА устанавливает PORT переменную
    port = int(os.environ.get("PORT", 8000))
    
    logger.info("=" * 60)
    logger.info("🚀 ЗАПУСК СЕРВЕРА ДЛЯ RAILWAY")
    logger.info("=" * 60)
    logger.info(f"📊 PORT из переменных: {port}")
    logger.info(f"🌐 Привязка к: 0.0.0.0:{port}")
    logger.info(f"🏢 Режим: {'RAILWAY PRODUCTION' if IS_RAILWAY else 'LOCAL DEVELOPMENT'}")
    logger.info(f"🔗 Ожидаемый публичный URL: {RAILWAY_PUBLIC_URL or 'Не установлен'}")
    logger.info("=" * 60)
    
    # Конфигурация для Railway
    config = {
        "app": "app:app",  # Строка импорта для uvicorn
        "host": "0.0.0.0",
        "port": port,
        "reload": False,  # На Railway всегда False
        "log_level": "info",
        "access_log": True,
        "timeout_keep_alive": 30,
        "workers": 1  # Для Railway рекомендуется 1 worker
    }
    
    if IS_RAILWAY:
        logger.info("⚙️  Конфигурация для Railway Production:")
        for key, value in config.items():
            logger.info(f"  {key}: {value}")
    
    # Запускаем сервер
    uvicorn.run(**config)

if __name__ == "__main__":
    start_server()
else:
    # Для импорта как модуль (например, gunicorn)
    # Railway иногда использует gunicorn
    logger.info("📦 Приложение загружено как модуль")
