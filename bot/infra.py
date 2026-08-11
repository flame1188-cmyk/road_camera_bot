"""bot.infra — инфраструктурные утилиты Telegram API.

Содержит:
  • _tg_retry — ретрай при TimedOut/NetworkError
  • _IsDocument — кастомный фильтр
  • _mark_api_down / _is_api_down / _log_memory
  • _get_user_lock — Lock на пользователя
  • _sanitize_error, _safe_edit, _send_long_message
  • _make_progress_bar

Выделено из единого bot.py (Phase 3-2). 100% pure.
"""
from bot._state import *

# Примечание: _MAX_TG_RETRIES, _TG_RETRY_DELAYS, _QA_HISTORY_MAX_MESSAGES,
# TG_MSG_LIMIT, _user_locks объявлены в bot._state (см. __all__).


async def _tg_retry(coro_factory, description="Telegram API"):
    """Выполняет вызов Telegram API с ретраем при TimedOut/NetworkError.

    Args:
        coro_factory: Вызываемый объект (lambda/func), возвращающий корутину.
                      Создаёт новую корутину при каждой попытке.
        description: Описание вызова для логов.
    """
    last_exc = None
    for attempt in range(_MAX_TG_RETRIES):
        try:
            return await coro_factory()
        except (TimedOut, NetworkError) as exc:
            last_exc = exc
            if attempt < _MAX_TG_RETRIES - 1:
                delay = _TG_RETRY_DELAYS[attempt]
                logger.warning(
                    f"{description}: {exc.__class__.__name__}. "
                    f"Попытка {attempt + 1}/{_MAX_TG_RETRIES}, "
                    f"повтор через {delay} сек..."
                )
                await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


class _IsDocument(filters.BaseFilter):
    """Фильтр для сообщений с прикреплённым документом (файлом).

    Используется вместо filters.Document, который в некоторых версиях
    python-telegram-bot разрешается в класс telegram.Document вместо фильтра.
    """
    def check_update(self, update):
        return bool(update.message and update.message.document is not None)


def _mark_api_down():
    """Помечает API ГИБДД как недоступный (на время текущей сессии)."""
    global _api_down
    _api_down = True


def _is_api_down() -> bool:
    return _api_down


def _log_memory(label: str) -> int:
    """Логирует текущее использование памяти и возвращает RSS в МБ.

    Используется для диагностики OOM: показывает RSS до/после очистки.
    """
    import resource
    try:
        rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # KB → MB
    except Exception:
        rss_mb = -1
    # tracemalloc удалён — замедлял генерацию Excel в 177-400x
    logger.info(f"[MEM] {label}: RSS={rss_mb:.1f} MB")
    return int(rss_mb) if rss_mb >= 0 else 0


def _get_user_lock(user_id: int) -> asyncio.Lock:
    """Возвращает (или создаёт) Lock для данного пользователя."""
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


def _sanitize_error(e: Exception) -> str:
    """Возвращает безопасное для пользователя описание ошибки."""
    name = type(e).__name__
    if isinstance(e, (TimedOut, NetworkError)):
        return f"{name}: Telegram API недоступен, попробуйте позже"
    if isinstance(e, ConnectionError):
        inner = e.__cause__
        if inner and "timed out" in str(inner).lower():
            return "Таймаут подключения к серверу ГИБДД"
        return "Сервер ГИБДД недоступен, попробуйте позже"
    return name  # только имя класса, без деталей


async def _safe_edit(
    query,
    text: str,
    reply_markup=None,
    parse_mode: str | None = None,
    description: str = "edit_message",
) -> None:
    """Безопасное редактирование сообщения с ретраем при TimedOut/NetworkError.

    Оборачивает query.edit_message_text в _tg_retry, чтобы временные
    проблемы с Telegram API не прерывали обработку callback.
    """
    await _tg_retry(
        lambda: query.edit_message_text(
            text=text, reply_markup=reply_markup, parse_mode=parse_mode,
        ),
        description,
    )


async def _send_long_message(
    bot,
    chat_id: int,
    text: str,
    parse_mode: str | None = None,
    reply_markup=None,
) -> None:
    """Отправляет текст, разбивая на части если он превышает TG_MSG_LIMIT.

    Разбивка происходит по границам абзацев (\\n\\n) для читаемости.
    reply_markup прикрепляется только к последнему сообщению.
    """
    if len(text) <= TG_MSG_LIMIT:
        await _tg_retry(lambda: bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode=parse_mode, reply_markup=reply_markup,
        ), "send_message (короткое)")
        return

    # Разбиваем по двойным переносам строк
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""
    for p in paragraphs:
        candidate = current + ("\n\n" if current else "") + p
        if len(candidate) > TG_MSG_LIMIT and current:
            chunks.append(current)
            current = p
        else:
            current = candidate
    if current:
        chunks.append(current)

    for i, chunk in enumerate(chunks):
        is_last = (i == len(chunks) - 1)
        await _tg_retry(
            lambda c=chunk, m=parse_mode, r=reply_markup if is_last else None:
                bot.send_message(
                    chat_id=chat_id, text=c,
                    parse_mode=m, reply_markup=r,
                ),
            f"send_message (часть {i + 1}/{len(chunks)})",
        )



def _make_progress_bar(current: int, total: int, width: int = 20) -> str:
    """Генерирует текстовую строку прогресса."""
    if total <= 1:
        return ""

    filled = int(width * current / total)
    empty = width - filled
    return f"[{'=' * filled}{' ' * empty}]"



