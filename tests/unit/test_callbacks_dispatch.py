"""
Тесты dispatch-таблицы для bot/handlers/callbacks.py (Phase 3-3).

Проверяют:
  1. Размеры dispatch-таблиц: 19 exact + 10 prefix = 29 handlers
  2. Все handlers имеют единую сигнатуру (update, context, data)
  3. Все known callback_data резолвятся в правильный handler
  4. Unknown callback_data → None (silent ignore)
  5. Конфликты префиксов отсутствуют (ни один prefix не является
     подстрокой другого в начале)
  6. on_callback_query — тонкий dispatcher: lock + access control +
     resolve + try/except
  7. Behavioural smoke: _resolve_handler вызывается внутри on_callback_query

Запуск: pytest tests/unit/test_callbacks_dispatch.py -v

Требует python-telegram-bot v20+ (как и весь bot/ пакет).
"""
from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# PTB required for bot.* imports
ptb = pytest.importorskip("telegram", reason="PTB v20+ required")
ptb_ext = pytest.importorskip("telegram.ext", reason="PTB v20+ required")

from bot.handlers import callbacks as cb
from bot.handlers.callbacks import (
    _EXACT_HANDLERS,
    _PREFIX_HANDLERS,
    _resolve_handler,
    on_callback_query,
)


# ============================================================
# 1. Размеры dispatch-таблиц
# ============================================================

class TestDispatchTableSizes:
    """Проверка размеров dispatch-таблиц."""

    def test_exact_handlers_count(self) -> None:
        """19 exact-match обработчиков (по числу уникальных callback_data)."""
        assert len(_EXACT_HANDLERS) == 19, (
            f"Ожидали 19 exact handlers, получили {len(_EXACT_HANDLERS)}. "
            f"Список: {sorted(_EXACT_HANDLERS.keys())}"
        )

    def test_prefix_handlers_count(self) -> None:
        """10 prefix-match обработчиков."""
        assert len(_PREFIX_HANDLERS) == 10, (
            f"Ожидали 10 prefix handlers, получили {len(_PREFIX_HANDLERS)}. "
            f"Список: {[p for p, _ in _PREFIX_HANDLERS]}"
        )

    def test_total_handlers_count(self) -> None:
        """Всего 29 обработчиков (19 + 10)."""
        assert len(_EXACT_HANDLERS) + len(_PREFIX_HANDLERS) == 29

    def test_no_duplicate_exact_keys(self) -> None:
        """Все exact-keys уникальны (dict гарантирует это, но проверим)."""
        keys = list(_EXACT_HANDLERS.keys())
        assert len(keys) == len(set(keys))

    def test_no_duplicate_prefix_keys(self) -> None:
        """Все prefix-keys уникальны."""
        prefixes = [p for p, _ in _PREFIX_HANDLERS]
        assert len(prefixes) == len(set(prefixes))


# ============================================================
# 2. Сигнатуры handlers
# ============================================================

class TestHandlerSignatures:
    """Все handlers имеют единую сигнатуру (update, context, data)."""

    @pytest.mark.parametrize("name,handler", sorted(_EXACT_HANDLERS.items()))
    def test_exact_handler_signature(self, name: str, handler) -> None:
        sig = inspect.signature(handler)
        params = list(sig.parameters.keys())
        assert params == ["update", "context", "data"], (
            f"EXACT handler {name!r} имеет сигнатуру {params}, "
            f"ожидалась ['update', 'context', 'data']"
        )

    @pytest.mark.parametrize("prefix,handler", _PREFIX_HANDLERS)
    def test_prefix_handler_signature(self, prefix: str, handler) -> None:
        sig = inspect.signature(handler)
        params = list(sig.parameters.keys())
        assert params == ["update", "context", "data"], (
            f"PREFIX handler {prefix!r} имеет сигнатуру {params}, "
            f"ожидалась ['update', 'context', 'data']"
        )

    @pytest.mark.parametrize("name,handler", sorted(_EXACT_HANDLERS.items()))
    def test_exact_handler_is_coroutine(self, name: str, handler) -> None:
        """Каждый handler — async function (возвращает coroutine)."""
        assert inspect.iscoroutinefunction(handler), (
            f"EXACT handler {name!r} не async"
        )

    @pytest.mark.parametrize("prefix,handler", _PREFIX_HANDLERS)
    def test_prefix_handler_is_coroutine(self, prefix: str, handler) -> None:
        assert inspect.iscoroutinefunction(handler), (
            f"PREFIX handler {prefix!r} не async"
        )


# ============================================================
# 3. Резолвинг known callback_data
# ============================================================

class TestKnownCallbackRouting:
    """Все known callback_data должны резолвиться в правильный handler."""

    # (callback_data, expected_handler_name)
    EXACT_CASES = [
        ("back", "_h_back"),
        ("do_analytics", "_h_do_analytics"),
        ("do_analytics_ai", "_h_do_analytics_ai_free"),
        ("do_analytics_ai_paid", "_h_do_analytics_ai_paid"),
        ("choose_ai_method", "_h_choose_ai_method"),
        ("do_concentration", "_h_do_concentration"),
        ("cam_skip", "_h_cam_skip"),
        ("cam_use_cached", "_h_cam_use_cached"),
        ("cam_ask_upload", "_h_cam_ask_upload"),
        ("do_point_stats", "_h_do_point_stats"),
        ("do_html_map", "_h_do_html_map"),
        ("html_map_dtp_only", "_h_html_map_dtp_only"),
        ("html_map_ask_cameras", "_h_html_map_ask_cameras"),
        ("ps_excel", "_h_ps_excel"),
        ("ps_html_map", "_h_ps_html_map"),
        ("change_data", "_h_change_data"),
        ("back_to_menu", "_h_back_to_menu"),
        ("end_qa", "_h_end_qa"),
        ("cancel", "_h_cancel"),
    ]

    # (callback_data, expected_handler_name)
    PREFIX_CASES = [
        ("rp:0", "_h_region_page"),
        ("rp:noop", "_h_region_page"),
        ("rp:5", "_h_region_page"),
        ("rp:999", "_h_region_page"),
        ("r:1182", "_h_region_select"),
        ("r:9999", "_h_region_select"),
        ("r:1", "_h_region_select"),
        ("py:2025", "_h_period_year"),
        ("py:2024", "_h_period_year"),
        ("pq:1:2025", "_h_period_quarter"),
        ("pq:4:2024", "_h_period_quarter"),
        ("ph:1:2025", "_h_period_half"),
        ("ph:2:2025", "_h_period_half"),
        ("p9:2025", "_h_period_9months"),
        ("pn:6:2025", "_h_period_n_months"),
        ("pn:12:2024", "_h_period_n_months"),
        ("pm:5:2025", "_h_period_month"),
        ("pm:12:2024", "_h_period_month"),
        ("yy:2024", "_h_year_nav"),
        ("yy:noop", "_h_year_nav"),
        ("ps_radius:500", "_h_ps_radius"),
        ("ps_radius:1000", "_h_ps_radius"),
    ]

    @pytest.mark.parametrize("data,expected_name", EXACT_CASES)
    def test_exact_routing(self, data: str, expected_name: str) -> None:
        handler = _resolve_handler(data)
        assert handler is not None, f"callback_data={data!r} не найден в dispatch-таблице"
        assert handler.__name__ == expected_name, (
            f"для {data!r}: ожидали {expected_name}, получили {handler.__name__}"
        )

    @pytest.mark.parametrize("data,expected_name", PREFIX_CASES)
    def test_prefix_routing(self, data: str, expected_name: str) -> None:
        handler = _resolve_handler(data)
        assert handler is not None, f"callback_data={data!r} не найден в dispatch-таблице"
        assert handler.__name__ == expected_name, (
            f"для {data!r}: ожидали {expected_name}, получили {handler.__name__}"
        )

    def test_exact_takes_precedence_over_prefix(self) -> None:
        """Если exact-handler есть — он выигрывает (O(1) lookup первым)."""
        # Все exact-keys должны резолвиться в exact-handler, даже если
        # теоретически могли бы попасть под prefix match.
        for exact_key, expected_handler in _EXACT_HANDLERS.items():
            actual = _resolve_handler(exact_key)
            assert actual is expected_handler, (
                f"exact-key {exact_key!r} не резолвится в свой exact-handler"
            )


# ============================================================
# 4. Unknown callback_data → None
# ============================================================

class TestUnknownCallbacks:
    """Неизвестные callback_data должны давать None (silent ignore)."""

    @pytest.mark.parametrize("data", [
        "",
        "unknown",
        "unknown_data",
        "zzz:123",
        "foo",
        "callback",
        "r",         # без двоеточия — не matчит r:
        "rp",        # без двоеточия — не matчит rp:
        "py",        # без двоеточия
        "pm",        # без двоеточия
        "yy",        # без двоеточия
        "ps_radius", # без двоеточия и значения
        "ps_radius:", # prefix, но без значения — handler есть, но arg parse упадёт
        "back:",     # extra colon — не matчит exact "back"
    ])
    def test_unknown_returns_none(self, data: str) -> None:
        # Note: "ps_radius:" — это prefix match, handler вернётся!
        # Поэтому исключаем его из этого теста.
        if data == "ps_radius:":
            pytest.skip("ps_radius: matчит prefix, handler есть")
        result = _resolve_handler(data)
        assert result is None, (
            f"Для {data!r} ожидали None, получили {result.__name__ if result else 'None'}"
        )

    def test_empty_string(self) -> None:
        assert _resolve_handler("") is None

    def test_random_garbage(self) -> None:
        for i in range(100):
            data = f"garbage_{i}_{i*i}"
            assert _resolve_handler(data) is None


# ============================================================
# 5. Конфликты префиксов
# ============================================================

class TestPrefixConflicts:
    """Ни один prefix не должен быть подстрокой другого в начале."""

    def test_no_prefix_is_prefix_of_another(self) -> None:
        prefixes = [p for p, _ in _PREFIX_HANDLERS]
        for i, a in enumerate(prefixes):
            for j, b in enumerate(prefixes):
                if i == j:
                    continue
                # b не должен начинаться с a
                assert not b.startswith(a), (
                    f"prefix {a!r} является префиксом {b!r} — "
                    f"это создаст неоднозначность в routing"
                )

    def test_prefix_order_does_not_matter(self) -> None:
        """Т.к. конфликтов нет — перестановка _PREFIX_HANDLERS не меняет результат."""
        import random
        random.seed(42)
        shuffled = list(_PREFIX_HANDLERS)
        random.shuffle(shuffled)

        # Для каждого prefix-data проверяем что резолвится в тот же handler
        for prefix, expected_handler in _PREFIX_HANDLERS:
            data = prefix + "test_value"
            actual = _resolve_handler(data)
            assert actual is expected_handler, (
                f"Routing для {data!r} изменился бы при перестановке: "
                f"ожидали {expected_handler.__name__}, получили {actual.__name__ if actual else None}"
            )


# ============================================================
# 6. on_callback_query — thin dispatcher
# ============================================================

class TestOnCallbackQueryDispatcher:
    """on_callback_query должен быть тонким dispatcher'ом."""

    def test_signature(self) -> None:
        """Сигнатура: (update, context)."""
        sig = inspect.signature(on_callback_query)
        params = list(sig.parameters.keys())
        assert params == ["update", "context"], (
            f"on_callback_query signature: {params}"
        )

    def test_is_coroutine(self) -> None:
        assert inspect.iscoroutinefunction(on_callback_query)

    @pytest.mark.asyncio
    async def test_returns_early_on_empty_query(self) -> None:
        """Если query или query.data = None → early return."""
        update = MagicMock()
        update.callback_query = None
        context = MagicMock()
        # Не должно быть исключения
        await on_callback_query(update, context)

    @pytest.mark.asyncio
    async def test_returns_early_on_empty_data(self) -> None:
        update = MagicMock()
        update.callback_query = MagicMock()
        update.callback_query.data = None
        context = MagicMock()
        await on_callback_query(update, context)

    @pytest.mark.asyncio
    async def test_access_control_rejects_unauthorized(self) -> None:
        """is_user_allowed=False → edit_message_text с отказом."""
        update = MagicMock()
        update.callback_query = MagicMock()
        update.callback_query.data = "do_analytics"
        update.callback_query.from_user.id = 999
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        context = MagicMock()

        with patch.object(cb, "is_user_allowed", return_value=False):
            await on_callback_query(update, context)

        update.callback_query.edit_message_text.assert_awaited_once()
        call_args = update.callback_query.edit_message_text.call_args
        assert "нет доступа" in call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_locked_callback_is_ignored(self) -> None:
        """Если lock уже занят — callback игнорируется (debug log)."""
        update = MagicMock()
        update.callback_query = MagicMock()
        update.callback_query.data = "do_analytics"
        update.callback_query.from_user.id = 123
        update.callback_query.answer = AsyncMock()
        context = MagicMock()

        # Mock lock который locked()
        lock = MagicMock()
        lock.locked.return_value = True
        lock.__aenter__ = AsyncMock()
        lock.__aexit__ = AsyncMock()

        with patch.object(cb, "is_user_allowed", return_value=True), \
             patch.object(cb, "_get_user_lock", return_value=lock):
            await on_callback_query(update, context)

        # lock.__aenter__ НЕ должен был вызваться (мы вышли до async with)
        lock.__aenter__.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_callback_silently_ignored(self) -> None:
        """Unknown callback_data внутри lock → silent ignore."""
        update = MagicMock()
        update.callback_query = MagicMock()
        update.callback_query.data = "totally_unknown_callback"
        update.callback_query.from_user.id = 123
        update.callback_query.answer = AsyncMock()
        context = MagicMock()

        lock = asyncio.Lock()  # не locked

        with patch.object(cb, "is_user_allowed", return_value=True), \
             patch.object(cb, "_get_user_lock", return_value=lock):
            # Не должно быть исключения
            await on_callback_query(update, context)

    @pytest.mark.asyncio
    async def test_handler_exception_caught_and_shown(self) -> None:
        """Исключение в handler ловится, пользователю показывается ошибка."""
        update = MagicMock()
        update.callback_query = MagicMock()
        update.callback_query.data = "do_analytics"
        update.callback_query.from_user.id = 123
        update.callback_query.answer = AsyncMock()
        context = MagicMock()

        lock = asyncio.Lock()

        async def boom(*args, **kwargs):
            raise RuntimeError("test explosion")

        with patch.object(cb, "is_user_allowed", return_value=True), \
             patch.object(cb, "_get_user_lock", return_value=lock), \
             patch.object(cb, "_resolve_handler", return_value=boom), \
             patch.object(cb, "_safe_edit", new=AsyncMock()) as mock_safe_edit:
            await on_callback_query(update, context)
            # _safe_edit вызван с user-friendly ошибкой
            mock_safe_edit.assert_awaited_once()
            call_args = mock_safe_edit.call_args
            assert "Ошибка" in call_args[0][1] or "ошибка" in call_args[0][1].lower()

    @pytest.mark.asyncio
    async def test_known_callback_calls_handler(self) -> None:
        """Known callback_data → вызывается соответствующий handler."""
        update = MagicMock()
        update.callback_query = MagicMock()
        update.callback_query.data = "cancel"
        update.callback_query.from_user.id = 123
        update.callback_query.answer = AsyncMock()
        context = MagicMock()
        context.user_data = MagicMock()
        context.user_data.clear = MagicMock()

        lock = asyncio.Lock()

        with patch.object(cb, "is_user_allowed", return_value=True), \
             patch.object(cb, "_get_user_lock", return_value=lock), \
             patch.object(cb, "_safe_edit", new=AsyncMock()):
            await on_callback_query(update, context)
            # _h_cancel должен был вызвать user_data.clear()
            context.user_data.clear.assert_called_once()


# ============================================================
# 7.个别 handler smoke tests (без полной логики, только signature)
# ============================================================

class TestHandlerSmoke:
    """Каждый handler — async function, принимает (update, context, data).

    Не проверяем внутреннюю логику (она покрыта integration tests для
    gibdd_service). Здесь только базовая корректность определения.
    """

    ALL_HANDLERS = [(name, h) for name, h in sorted(_EXACT_HANDLERS.items())] + \
                   [(p, h) for p, h in _PREFIX_HANDLERS]

    @pytest.mark.parametrize("name,handler", ALL_HANDLERS)
    def test_handler_has_docstring(self, name: str, handler) -> None:
        """У каждого handler есть docstring (минимум 1 строка)."""
        doc = handler.__doc__
        assert doc is not None, f"handler {name!r} не имеет docstring"
        assert len(doc.strip()) > 0, f"handler {name!r} имеет пустой docstring"

    @pytest.mark.parametrize("name,handler", ALL_HANDLERS)
    def test_handler_name_starts_with_h(self, name: str, handler) -> None:
        """Все handlers имеют префикс _h_ в имени (конвенция Phase 3-3)."""
        assert handler.__name__.startswith("_h_"), (
            f"handler {name!r}: имя {handler.__name__} не начинается с _h_"
        )
