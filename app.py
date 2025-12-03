# app.py в корневой папке
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from contextlib import asynccontextmanager
import os
import json
from datetime import datetime, timedelta
from pathlib import Path
import shutil
import sqlite3

# Lifespan events (вместо on_event)
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("✅ Сервер запущен!")
    print(f"📁 База данных: {DB_PATH}")
    print(f"🌐 Адрес: http://localhost:8000")
    yield
    # Shutdown
    print("👋 Сервер остановлен")

app = FastAPI(lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Конфигурация
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MEDIA_DIR = DATA_DIR / "media"

DATA_DIR.mkdir(exist_ok=True)
MEDIA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "chat.db"

# Инициализация БД
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Таблица пользователей
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
    
    # Таблица чатов
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
    
    # Таблица сообщений
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

init_db()

# Статические файлы
app.mount("/static", StaticFiles(directory="client"), name="static")
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    if os.path.exists("client/index.html"):
        with open("client/index.html", "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Telegram Chat Mini App</h1><p>Создайте файл client/index.html</p>"

@app.get("/api/health")
async def health_check():
    db_exists = os.path.exists(DB_PATH)
    media_exists = os.path.exists(MEDIA_DIR)
    
    return {
        "status": "healthy",
        "database": "connected" if db_exists else "missing",
        "media_dir": "exists" if media_exists else "missing",
        "timestamp": datetime.now().isoformat()
    }

@app.post("/api/auth/telegram")
async def auth_telegram(data: dict):
    user_data = data.get("user", {})
    telegram_id = user_data.get("id")
    
    if not telegram_id:
        raise HTTPException(400, "No user ID")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Ищем пользователя
    cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    user = cursor.fetchone()
    
    if user:
        # Обновляем last_seen
        cursor.execute(
            "UPDATE users SET last_seen = CURRENT_TIMESTAMP WHERE id = ?",
            (user[0],)
        )
        conn.commit()
        
        user_dict = {
            "id": user[0],
            "telegram_id": user[1],
            "username": user[2],
            "first_name": user[3],
            "avatar_url": user[5],
            "is_admin": bool(user[7]),
            "is_banned": bool(user[8])
        }
    else:
        # Создаем нового
        cursor.execute('''
        INSERT INTO users (telegram_id, username, first_name, last_name, avatar_url, is_bot)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            telegram_id,
            user_data.get("username"),
            user_data.get("first_name"),
            user_data.get("last_name"),
            user_data.get("photo_url"),
            user_data.get("is_bot", False)
        ))
        conn.commit()
        
        user_dict = {
            "id": cursor.lastrowid,
            "telegram_id": telegram_id,
            "username": user_data.get("username"),
            "first_name": user_data.get("first_name"),
            "avatar_url": user_data.get("photo_url"),
            "is_admin": False,
            "is_banned": False
        }
    
    conn.close()
    return {"success": True, "user": user_dict}

@app.get("/api/chat/messages")
async def get_messages(chat_id: int = 1, limit: int = 50):
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
    
    return {"success": True, "messages": messages}

@app.post("/api/chat/send")
async def send_message(
    user_id: int = Form(...),
    content: str = Form(""),
    file: UploadFile = File(None)
):
    # Проверяем пользователя
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT is_banned, muted_until FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    
    if not user:
        conn.close()
        raise HTTPException(404, "User not found")
    
    if user[0]:  # is_banned
        conn.close()
        raise HTTPException(403, "User is banned")
    
    if user[1]:  # muted_until
        muted_until = datetime.fromisoformat(user[1])
        if muted_until > datetime.now():
            conn.close()
            raise HTTPException(403, "User is muted")
    
    message_type = "text"
    media_filename = None
    file_size = 0
    
    if file and file.filename:
        # Сохраняем файл
        ext = os.path.splitext(file.filename)[1] or ".bin"
        media_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{user_id}{ext}"
        file_path = MEDIA_DIR / media_filename
        
        file_content = await file.read()
        file_size = len(file_content)
        
        with open(file_path, "wb") as f:
            f.write(file_content)
        
        if file.content_type.startswith("image/"):
            message_type = "photo"
        elif file.content_type.startswith("audio/"):
            message_type = "voice"
        else:
            message_type = "file"
    
    # Сохраняем сообщение
    cursor.execute('''
    INSERT INTO messages (chat_room_id, user_id, message_type, content, media_filename, media_size)
    VALUES (1, ?, ?, ?, ?, ?)
    ''', (user_id, message_type, content, media_filename, file_size))
    
    conn.commit()
    message_id = cursor.lastrowid
    
    # Получаем данные пользователя
    cursor.execute("SELECT username, first_name, avatar_url FROM users WHERE id = ?", (user_id,))
    user_data = cursor.fetchone()
    
    conn.close()
    
    return {
        "success": True,
        "message": {
            "id": message_id,
            "user": {
                "id": user_id,
                "username": user_data[0],
                "first_name": user_data[1],
                "avatar_url": user_data[2]
            },
            "content": content,
            "type": message_type,
            "media_url": f"/media/{media_filename}" if media_filename else None,
            "created_at": datetime.now().isoformat()
        }
    }

@app.post("/api/moderation/mute")
async def mute_user(
    user_id: int = Form(...),
    moderator_id: int = Form(...),
    duration_minutes: int = Form(60),
    reason: str = Form("")
):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Проверяем что модератор - админ
    cursor.execute("SELECT is_admin FROM users WHERE id = ?", (moderator_id,))
    moderator = cursor.fetchone()
    
    if not moderator or not moderator[0]:
        conn.close()
        raise HTTPException(403, "Not an admin")
    
    # Мьютим пользователя
    muted_until = datetime.now() + timedelta(minutes=duration_minutes)
    cursor.execute(
        "UPDATE users SET muted_until = ? WHERE id = ?",
        (muted_until.isoformat(), user_id)
    )
    
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "message": f"User muted for {duration_minutes} minutes",
        "muted_until": muted_until.isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)