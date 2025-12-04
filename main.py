#!/usr/bin/env python3
"""
Точка входа для Railway
"""

import os
import sys

# Добавляем текущую директорию в путь Python
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Импортируем app из app.py
from app import app

# Это нужно для Railway
application = app  # для совместимости

if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    
    print(f"🚀 Запуск через main.py на {host}:{port}")
    
    uvicorn.run(
        "app:app",  # Важно: строка для импорта
        host=host,
        port=port,
        reload=False
    )
