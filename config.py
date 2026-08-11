"""
Конфигурация проекта Telegram-бота для выгрузки данных ДТП с stat.gibdd.ru.
Все ключи и настройки читаются из переменных окружения или файла .env
"""

import os
from dotenv import load_dotenv

# Загружаем переменные из файла .env (если он существует)
load_dotenv()


# ========================
# Telegram Bot
# ========================
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

# ID пользователей, которым разрешено использовать бота (через запятую)
# Оставьте пустым, чтобы разрешить всем
ALLOWED_USER_IDS: list[int] = []
_raw_allowed = os.getenv("ALLOWED_USER_IDS", "")
if _raw_allowed:
    ALLOWED_USER_IDS = [int(uid.strip()) for uid in _raw_allowed.split(",")]

# ID администраторов для системных уведомлений (через запятую).
# Сюда будут падать алерты от monitor_cards_cache.sh и других
# внешних скриптов мониторинга (через Telegram Bot API).
# Узнать свой ID можно у @userinfobot.
# Оставьте пустым, чтобы отключить уведомления.
ADMIN_TELEGRAM_IDS: list[int] = []
_raw_admins = os.getenv("ADMIN_TELEGRAM_IDS", "")
if _raw_admins:
    ADMIN_TELEGRAM_IDS = [int(uid.strip()) for uid in _raw_admins.split(",")]


# ========================
# Сеть
# ========================
# Таймаут запросов к API stat.gibdd.ru (в секундах).
# API ГИБДД может отвечать медленно при больших выборках, ставьте 60-120.
TARGET_API_TIMEOUT: int = int(os.getenv("TARGET_API_TIMEOUT", "120"))

# Прокси (если нужен для корпоративной сети)
HTTP_PROXY: str = os.getenv("HTTP_PROXY", "")
HTTPS_PROXY: str = os.getenv("HTTPS_PROXY", "")


# ========================
# LLM — бесплатный (ZhipuAI / GLM)
# ========================
# API-ключ для ZhipuAI (GLM). Получить: https://open.bigmodel.cn
# Если не задан — кнопка "Анализ с ИИ" будет недоступна
LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")

# Модель LLM (по умолчанию glm-4.7-flash — бесплатная, безлимитная, 200K контекст)
# Другие бесплатные: glm-4.5-flash, glm-4-flash-250414
LLM_MODEL: str = os.getenv("LLM_MODEL", "glm-4.7-flash")


# ========================
# LLM — платный (OpenAI-совместимый агрегатор, напр. AItunnel)
# ========================
# API-ключ для платного LLM-провайдера (AItunnel, OpenRouter и т.д.)
# Если не задан — опция "Полный (платный)" не будет показываться
LLM_PAID_API_KEY: str = os.getenv("LLM_PAID_API_KEY", "")

# URL API платного провайдера (без /chat/completions — добавляется автоматически)
# Примеры:
#   AItunnel:  https://api.aitunnel.ru/v1
#   OpenRouter: https://openrouter.ai/api/v1
LLM_PAID_API_URL: str = os.getenv("LLM_PAID_API_URL", "https://api.aitunnel.ru/v1")

# Модель платного LLM
# Примеры:
#   AItunnel:   deepseek-v4-flash, deepseek-v3, gpt-4o, claude-4-sonnet
#   OpenRouter: google/gemini-2.5-flash, deepseek/deepseek-chat
LLM_PAID_MODEL: str = os.getenv("LLM_PAID_MODEL", "deepseek-v4-flash")


# ========================
# Общие настройки LLM
# ========================
# Включать ли поиск новостей из открытых источников (Google News RSS + DuckDuckGo)
# Если "false" — нейросеть будет анализировать только данные stat.gibdd.ru
ENABLE_NEWS_SEARCH: bool = os.getenv("ENABLE_NEWS_SEARCH", "true").lower() == "true"


# ========================
# Логирование
# ========================
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


# ========================
# PostgreSQL-кэш (Этап 3+)
# ========================
# TTL кэша карточек ДТП в секундах.
# По умолчанию 7 дней (604800 сек) — карточки ДТП за прошлый период не
# меняются (это исторические данные, ГИБДД их не правит). Sprint 3.1:
# подняли с 1 часа до 7 дней, чтобы старые задачи оставались рабочими
# после суточного/недельного перерыва (task.cards восстанавливается из
# этого кэша через ensure_cards).
# Рекомендации:
#   604800   (7 дней)  — production default (закрытые периоды, история)
#   86400    (24 часа) — если нужно частое обновление текущего периода
#   3600     (1 час)   — отладка/тестирование
#   300      (5 мин)   — быстрая инвалидация при разработке
CARDS_CACHE_TTL_SECONDS: int = int(os.getenv("CARDS_CACHE_TTL_SECONDS", "604800"))

# Будущее: TTL кэша очагов (Этап 4 — пока не используется).
# Очаги стабильнее карточек — TTL по умолчанию 6 часов.
CLUSTERS_CACHE_TTL_SECONDS: int = int(os.getenv("CLUSTERS_CACHE_TTL_SECONDS", "21600"))

# Этап 5: TTL кэша готовых Excel-файлов (Файл 1 ДТП + Файл 2 участники)
# в PostgreSQL. Excel — производное от cards (через gibdd_parser +
# excel_generator), поэтому TTL должен быть ≤ CARDS_CACHE_TTL_SECONDS
# (если cards протухли — Excel всё ещё валиден, но при cache miss cards
# перечитаются и excel_cache обновится).
# По умолчанию 24 часа (86400 сек) — совпадает с рекомендуемым TTL cards
# для закрытых периодов. Экономия ~5-8 сек на каждого пользователя,
# который запрашивает тот же регион+период.
EXCEL_CACHE_TTL_SECONDS: int = int(os.getenv("EXCEL_CACHE_TTL_SECONDS", "86400"))


# ========================
# Sprint 2: LLM_SEMAPHORE + LLM cache
# ========================

# LLM_SEMAPHORE — лимит одновременных LLM-вызовов в одном процессе.
# Защищает от 429 Too Many Requests на free-тарифе (GLM-4.7-Flash RPM~30).
# При превышении лимита coroutine ждёт в очереди (FIFO).
#   - free-тариф: рекомендуется 2 (безопасно для RPM=30)
#   - paid-тариф (DeepSeek): можно 5+ (RPM=200)
# При переходе на paid — выставьте LLM_MAX_CONCURRENT=5.
LLM_MAX_CONCURRENT: int = int(os.getenv("LLM_MAX_CONCURRENT", "2"))

# LLM cache — TTL кэшированных summary в PostgreSQL.
# По умолчанию 24 часа (86400 сек). Summary — производное от
# (cards + clusters + cross_tables + SYSTEM_PROMPT), все детерминированы.
# Если меняется prompt_hash — кэш инвалидируется автоматически, поэтому
# TTL можно держать длинным.
LLM_CACHE_TTL_SECONDS: int = int(os.getenv("LLM_CACHE_TTL_SECONDS", "86400"))

# LLM cache version — позволяет принудительно инвалидировать ВЕСЬ кэш
# (например, при глобальном изменении SYSTEM_PROMPT, которое не отразилось
# в prompt_hash, или при смене модели). Увеличьте на 1 — все старые записи
# перестанут матчится по cache_key.
LLM_CACHE_VERSION: str = os.getenv("LLM_CACHE_VERSION", "1")


# ========================
# Валидация
# ========================
def validate_config() -> list[str]:
    """Проверяет, что все обязательные настройки заданы. Возвращает список ошибок."""
    errors = []

    if not TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN не задан. Получите его у @BotFather в Telegram.")

    return errors
