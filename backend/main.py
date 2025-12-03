from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
import os
import json
from datetime import datetime, timedelta
from pathlib import Path
import shutil
import asyncio
from typing import Dict, Set, List
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from backend.config import Config
from backend.database import engine, get_db
from backend.models import Base, User, Message, ChatRoom

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

# WebSocket соединения
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, Dict[str, WebSocket]] = {}  # chat_id -> {user_id: websocket}
        self.user_chats: Dict[int, Set[int]] = {}  # user_id -> set of chat_ids
        
    async def connect(self, websocket: WebSocket, chat_id: int, user_id: int):
        await websocket.accept()
        
        if chat_id not in self.active_connections:
            self.active_connections[chat_id] = {}
        
        self.active_connections[chat_id][user_id] = websocket
        
        if user_id not in self.user_chats:
            self.user_chats[user_id] = set()
        self.user_chats[user_id].add(chat_id)
        
        # Уведомляем чат о новом пользователе
        await self.broadcast_to_chat(chat_id, {
            "type": "user_joined",
            "user_id": user_id,
            "timestamp": datetime.utcnow().isoformat(),
            "online_count": len(self.active_connections[chat_id])
        }, exclude_user=user_id)
        
        # Отправляем текущий онлайн пользователю
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
            
            # Уведомляем чат
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

@app.on_event("startup")
async def startup():
    # Создаем таблицы
    Base.metadata.create_all(bind=engine)
    
    # Создаем общий чат если его нет
    db = next(get_db())
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
    
    # Запускаем задачу очистки старых медиа
    asyncio.create_task(cleanup_old_media_task())

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

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    """Отдаем главную страницу"""
    with open("client/index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/api/health")
async def health_check(db: Session = Depends(get_db)):
    """Проверка здоровья сервера"""
    try:
        # Проверка базы данных
        db.execute("SELECT 1")
        
        # Статистика
        users_count = db.query(func.count(User.id)).scalar() or 0
        messages_count = db.query(func.count(Message.id)).scalar() or 0
        today_messages = db.query(func.count(Message.id)).filter(
            func.date(Message.created_at) == datetime.utcnow().date()
        ).scalar() or 0
        
        # Размер базы
        db_size = 0
        if os.path.exists(Config.SQLITE_PATH):
            db_size = os.path.getsize(Config.SQLITE_PATH)
        
        # Размер медиа
        media_size = 0
        media_path = Path(Config.MEDIA_PATH)
        if media_path.exists():
            media_size = sum(f.stat().st_size for f in media_path.rglob('*') if f.is_file())
        
        return {
            "status": "healthy",
            "environment": Config.ENVIRONMENT,
            "database": "connected",
            "stats": {
                "users": users_count,
                "messages": messages_count,
                "today_messages": today_messages,
                "db_size_mb": round(db_size / (1024 * 1024), 2),
                "media_size_mb": round(media_size / (1024 * 1024), 2)
            },
            "websocket": {
                "active_chats": len(manager.active_connections),
                "total_users": len(manager.user_chats)
            },
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Health check failed: {str(e)}")

@app.post("/api/auth/telegram")
async def auth_telegram(data: dict, db: Session = Depends(get_db)):
    """Авторизация через Telegram Web App"""
    try:
        user_data = data.get("user", {})
        telegram_id = user_data.get("id")
        
        if not telegram_id:
            raise HTTPException(status_code=400, detail="No user data provided")
        
        # Валидация данных (в реальном проекте проверяйте хэш от Telegram)
        
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
                "last_name": user.last_name,
                "avatar_url": user.avatar_url,
                "is_admin": user.is_admin,
                "is_banned": user.is_banned,
                "muted_until": user.muted_until.isoformat() if user.muted_until else None
            }
        })
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Authentication error: {str(e)}")

@app.get("/api/chat/messages")
async def get_messages(
    chat_id: int = 1,
    limit: int = 50,
    before_id: int = None,
    db: Session = Depends(get_db)
):
    """Получить сообщения чата с пагинацией"""
    try:
        query = db.query(Message).join(User).filter(
            Message.chat_room_id == chat_id,
            Message.is_deleted == False
        )
        
        if before_id:
            query = query.filter(Message.id < before_id)
        
        messages = query.order_by(Message.created_at.desc()).limit(limit).all()
        
        has_more = False
        if messages and len(messages) == limit:
            # Проверяем, есть ли еще сообщения
            oldest_id = min(msg.id for msg in messages)
            has_more = db.query(Message.id).filter(
                Message.chat_room_id == chat_id,
                Message.id < oldest_id,
                Message.is_deleted == False
            ).first() is not None
        
        return {
            "success": True,
            "messages": [
                {
                    "id": msg.id,
                    "user": {
                        "id": msg.user.id,
                        "telegram_id": msg.user.telegram_id,
                        "username": msg.user.username,
                        "first_name": msg.user.first_name,
                        "last_name": msg.user.last_name,
                        "avatar_url": msg.user.avatar_url,
                        "is_admin": msg.user.is_admin
                    },
                    "content": msg.content,
                    "type": msg.message_type,
                    "media_url": f"/media/{msg.media_filename}" if msg.media_filename else None,
                    "media_size": msg.media_size,
                    "mentions": json.loads(msg.mentions) if msg.mentions else [],
                    "reply_to": msg.reply_to_id,
                    "is_edited": msg.is_edited,
                    "created_at": msg.created_at.isoformat(),
                    "updated_at": msg.updated_at.isoformat() if msg.updated_at else None
                }
                for msg in reversed(messages)  # Возвращаем в правильном порядке
            ],
            "has_more": has_more,
            "chat_id": chat_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load messages: {str(e)}")

@app.post("/api/chat/send")
async def send_message(
    user_id: int = Form(...),
    content: str = Form(""),
    chat_id: int = Form(1),
    reply_to_id: int = Form(None),
    mentions: str = Form("[]"),
    file: UploadFile = File(None),
    db: Session = Depends(get_db)
):
    """Отправить сообщение в чат"""
    try:
        # Проверяем пользователя
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        if user.is_banned:
            raise HTTPException(status_code=403, detail="User is banned")
        
        if user.muted_until and user.muted_until > datetime.utcnow():
            raise HTTPException(
                status_code=403,
                detail=f"User is muted until {user.muted_until}"
            )
        
        # Проверяем чат
        chat = db.query(ChatRoom).filter(ChatRoom.id == chat_id).first()
        if not chat:
            raise HTTPException(status_code=404, detail="Chat not found")
        
        message_type = "text"
        media_filename = None
        media_size = 0
        
        # Обработка файла
        if file and file.filename:
            # Проверяем размер
            file_content = await file.read()
            media_size = len(file_content)
            
            if media_size > Config.MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail=f"File too large. Max size: {Config.MAX_FILE_SIZE // (1024*1024)}MB"
                )
            
            # Определяем тип и расширение
            if file.content_type.startswith("image/"):
                message_type = "photo"
                ext = ".jpg"
            elif file.content_type.startswith("audio/"):
                message_type = "voice"
                ext = ".ogg"
            elif file.content_type.startswith("video/"):
                message_type = "video"
                ext = ".mp4"
            else:
                message_type = "file"
                ext = Path(file.filename).suffix or ".bin"
            
            # Сохраняем файл
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
            media_filename = f"{message_type}_{timestamp}_{user_id}{ext}"
            file_path = Path(Config.MEDIA_PATH) / media_filename
            
            with open(file_path, "wb") as f:
                f.write(file_content)
        
        # Парсим mentions
        try:
            mentions_list = json.loads(mentions)
        except:
            mentions_list = []
        
        # Сохраняем сообщение в базу
        message = Message(
            chat_room_id=chat_id,
            user_id=user_id,
            message_type=message_type,
            content=content,
            media_filename=media_filename,
            media_size=media_size,
            mentions=json.dumps(mentions_list),
            reply_to_id=reply_to_id,
            created_at=datetime.utcnow()
        )
        
        db.add(message)
        db.commit()
        db.refresh(message)
        
        # Получаем полные данные сообщения для рассылки
        message_data = {
            "id": message.id,
            "user": {
                "id": user.id,
                "telegram_id": user.telegram_id,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "avatar_url": user.avatar_url,
                "is_admin": user.is_admin
            },
            "content": content,
            "type": message_type,
            "media_url": f"/media/{media_filename}" if media_filename else None,
            "media_size": media_size,
            "mentions": mentions_list,
            "reply_to": reply_to_id,
            "is_edited": False,
            "created_at": message.created_at.isoformat(),
            "chat_id": chat_id
        }
        
        # Отправляем через WebSocket
        await manager.broadcast_to_chat(chat_id, {
            "type": "new_message",
            "message": message_data
        })
        
        # Уведомляем упомянутых пользователей
        for mention in mentions_list:
            mentioned_user_id = mention.get("user_id")
            if mentioned_user_id:
                await manager.send_to_user(mentioned_user_id, {
                    "type": "mention",
                    "message": message_data,
                    "mentioned_by": user.first_name
                })
        
        return JSONResponse({
            "success": True,
            "message": message_data
        })
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send message: {str(e)}")

@app.post("/api/moderation/mute")
async def mute_user(
    user_id: int = Form(...),
    moderator_id: int = Form(...),
    duration_minutes: int = Form(60),
    reason: str = Form(""),
    db: Session = Depends(get_db)
):
    """Замутить пользователя"""
    try:
        # Проверяем права модератора
        moderator = db.query(User).filter(
            User.id == moderator_id,
            User.is_admin == True
        ).first()
        
        if not moderator:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        
        # Находим пользователя
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Устанавливаем мут
        muted_until = datetime.utcnow() + timedelta(minutes=duration_minutes)
        user.muted_until = muted_until
        db.commit()
        
        # Уведомляем пользователя через WebSocket
        await manager.send_to_user(user_id, {
            "type": "muted",
            "duration_minutes": duration_minutes,
            "reason": reason,
            "muted_until": muted_until.isoformat(),
            "moderator": moderator.first_name
        })
        
        # Уведомляем чат
        await manager.broadcast_to_chat(1, {
            "type": "user_muted",
            "user_id": user_id,
            "user_name": user.first_name,
            "duration_minutes": duration_minutes,
            "reason": reason,
            "moderator": moderator.first_name
        })
        
        return JSONResponse({
            "success": True,
            "message": f"User {user.first_name} muted for {duration_minutes} minutes",
            "muted_until": muted_until.isoformat()
        })
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to mute user: {str(e)}")

@app.post("/api/moderation/ban")
async def ban_user(
    user_id: int = Form(...),
    moderator_id: int = Form(...),
    reason: str = Form(""),
    db: Session = Depends(get_db)
):
    """Забанить пользователя"""
    try:
        moderator = db.query(User).filter(
            User.id == moderator_id,
            User.is_admin == True
        ).first()
        
        if not moderator:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        user.is_banned = True
        db.commit()
        
        # Отключаем пользователя от WebSocket
        manager.disconnect(1, user_id)
        
        await manager.broadcast_to_chat(1, {
            "type": "user_banned",
            "user_id": user_id,
            "user_name": user.first_name,
            "reason": reason,
            "moderator": moderator.first_name
        })
        
        return JSONResponse({
            "success": True,
            "message": f"User {user.first_name} banned"
        })
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to ban user: {str(e)}")

@app.get("/api/users/online")
async def get_online_users():
    """Получить онлайн пользователей в основном чате"""
    try:
        if 1 in manager.active_connections:
            online_user_ids = list(manager.active_connections[1].keys())
            
            # Получаем информацию о пользователях
            db = next(get_db())
            users = db.query(User).filter(User.id.in_(online_user_ids)).all()
            
            return JSONResponse({
                "success": True,
                "users": [
                    {
                        "id": user.id,
                        "first_name": user.first_name,
                        "username": user.username,
                        "avatar_url": user.avatar_url,
                        "last_seen": user.last_seen.isoformat() if user.last_seen else None
                    }
                    for user in users
                ],
                "count": len(online_user_ids)
            })
        else:
            return JSONResponse({
                "success": True,
                "users": [],
                "count": 0
            })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get online users: {str(e)}")

@app.websocket("/ws/{chat_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, chat_id: int, user_id: int):
    """WebSocket endpoint для реального времени"""
    try:
        # Подключаем пользователя
        await manager.connect(websocket, chat_id, user_id)
        
        # Обновляем last_seen
        db = next(get_db())
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.last_seen = datetime.utcnow()
            db.commit()
        
        # Слушаем сообщения от клиента
        while True:
            data = await websocket.receive_json()
            message_type = data.get("type")
            
            if message_type == "typing":
                # Пересылаем статус "печатает"
                await manager.broadcast_to_chat(chat_id, {
                    "type": "user_typing",
                    "user_id": user_id,
                    "user_name": user.first_name if user else "Unknown"
                }, exclude_user=user_id)
                
            elif message_type == "read_receipt":
                # Подтверждение прочтения
                message_id = data.get("message_id")
                await manager.broadcast_to_chat(chat_id, {
                    "type": "message_read",
                    "user_id": user_id,
                    "message_id": message_id
                }, exclude_user=user_id)
                
    except WebSocketDisconnect:
        manager.disconnect(chat_id, user_id)
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(chat_id, user_id)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=Config.HOST,
        port=Config.PORT,
        reload=Config.ENVIRONMENT == "development"
    )