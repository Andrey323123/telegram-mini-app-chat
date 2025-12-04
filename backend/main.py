#!/usr/bin/env python3
"""
Telegram Mini App Chat - Главный файл запуска для Railway
"""

import os
import sys
import logging
from pathlib import Path

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def check_environment():
    """Проверка окружения"""
    logger.info("🔍 Проверка окружения...")
    
    # Проверяем переменные окружения
    required_vars = ["BOT_TOKEN", "BOT_USERNAME"]
    for var in required_vars:
        if not os.getenv(var):
            logger.warning(f"⚠️  Переменная окружения {var} не установлена")
    
    # Проверяем структуру папок
    required_dirs = ["data", "data/media", "client"]
    for dir_name in required_dirs:
        dir_path = Path(dir_name)
        if not dir_path.exists():
            logger.info(f"📁 Создаю папку: {dir_name}")
            dir_path.mkdir(parents=True, exist_ok=True)
    
    return True

def install_dependencies():
    """Установка зависимостей"""
    logger.info("📦 Проверка зависимостей...")
    
    requirements_file = Path("requirements.txt")
    if not requirements_file.exists():
        logger.error("❌ Файл requirements.txt не найден!")
        return False
    
    try:
        import fastapi
        import uvicorn
        import sqlalchemy
        import python_dotenv
        logger.info("✅ Все зависимости установлены")
        return True
    except ImportError as e:
        logger.error(f"❌ Отсутствует зависимость: {e}")
        logger.info("Установите зависимости: pip install -r requirements.txt")
        return False

def main():
    """Основная функция запуска"""
    logger.info("=" * 50)
    logger.info("🚀 Telegram Mini App Chat - Railway Deployment")
    logger.info("=" * 50)
    
    # Проверяем окружение
    if not check_environment():
        sys.exit(1)
    
    # Проверяем зависимости
    if not install_dependencies():
        sys.exit(1)
    
    # Запускаем приложение
    try:
        logger.info("🎯 Запуск FastAPI приложения...")
        
        # Параметры запуска
        host = os.getenv("HOST", "0.0.0.0")
        port = int(os.getenv("PORT", 8000))
        
        logger.info(f"🌐 Хост: {host}")
        logger.info(f"🔌 Порт: {port}")
        logger.info(f"🤖 Бот: @{os.getenv('BOT_USERNAME', 'N/A')}")
        logger.info(f"🏢 Режим: {'Production' if os.getenv('RAILWAY_ENVIRONMENT') else 'Development'}")
        
        # Импортируем и запускаем приложение
        from app import app
        
        import uvicorn
        uvicorn.run(
            "app:app",
            host=host,
            port=port,
            reload=False,  # На Railway лучше отключить
            log_level="info",
            access_log=True
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка при запуске: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
