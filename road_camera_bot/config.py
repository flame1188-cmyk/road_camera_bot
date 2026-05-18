"""
Конфигурация приложения. Читает переменные окружения и экспортирует константы.
"""

import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ========================
# Основные настройки
# ========================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

_raw_allowed = os.getenv("ALLOWED_USER_IDS", "").strip()
if _raw_allowed:
    ALLOWED_USER_IDS = set(
        int(uid.strip()) for uid in _raw_allowed.split(",") if uid.strip().isdigit()
    )
else:
    ALLOWED_USER_IDS = set()

# ========================
# VLM / Vision API
# ========================

VLM_API_KEY = os.getenv("VLM_API_KEY", "")
VLM_API_URL = os.getenv("VLM_API_URL", "https://open.bigmodel.cn/api/paas/v4/chat/completions")
VLM_MODEL = os.getenv("VLM_MODEL", "glm-4.6v-flash")

# ========================
# LLM / Text API
# ========================

LLM_API_KEY = os.getenv("LLM_API_KEY", "")

# ========================
# Mapillary
# ========================

MAPILLARY_ACCESS_TOKEN = os.getenv("MAPILLARY_ACCESS_TOKEN", "")

# ========================
# Прокси
# ========================

HTTP_PROXY = os.getenv("HTTP_PROXY", "")
HTTPS_PROXY = os.getenv("HTTPS_PROXY", "")

# ========================
# Прочее
# ========================

TARGET_API_TIMEOUT = int(os.getenv("TARGET_API_TIMEOUT", "60"))
ENABLE_NEWS_SEARCH = os.getenv("ENABLE_NEWS_SEARCH", "false").lower() in ("true", "1", "yes")


def validate_config() -> None:
    """Проверяет настройки и выводит предупреждения для отсутствующих ключей."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN не задан — бот не запустится")
    if not VLM_API_KEY:
        logger.warning("VLM_API_KEY не задан — анализ изображений через VLM недоступен")
    if not LLM_API_KEY:
        logger.warning("LLM_API_KEY не задан — AI-аналитика и вопросы недоступны")
    if not MAPILLARY_ACCESS_TOKEN:
        logger.warning("MAPILLARY_ACCESS_TOKEN не задан — уличные фото Mapillary недоступны")
    if HTTP_PROXY or HTTPS_PROXY:
        logger.info(f"Прокси: HTTP={HTTP_PROXY or 'нет'}, HTTPS={HTTPS_PROXY or 'нет'}")
