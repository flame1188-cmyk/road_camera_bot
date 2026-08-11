"""
Тесты telegram_auth.py — проверка подписи Telegram WebApp initData.

Покрытие:
  - _verify_init_data: валидная подпись, неверная подпись, нет hash, просрочка
  - _extract_user: валидный user JSON, нет user, невалидный JSON
  - _check_whitelist: пустой whitelist (всем можно), пользователь в списке, не в списке
  - get_current_user: из query, из header, нет initData → 401, невалидная подпись → 401

Алгоритм Telegram:
  data_check_string = "k1=v1\nk2=v2\n..." (sorted by key)
  secret_key = HMAC-SHA256("WebAppData", bot_token)
  expected_hash = HMAC-SHA256(secret_key, data_check_string)
"""
import json
import time

import pytest
from fastapi import HTTPException


# ============================================================
# _verify_init_data
# ============================================================
class TestVerifyInitData:
    def test_valid_signature_returns_parsed_dict(self, telegram_init_data_factory, test_bot_token):
        from backend.telegram_auth import _verify_init_data, settings

        init_data = telegram_init_data_factory(user_id=123, bot_token=settings.telegram_bot_token)
        result = _verify_init_data(init_data, settings.telegram_bot_token)

        assert result is not None
        assert "user" in result
        assert "auth_date" in result
        assert "hash" not in result, "hash должен быть удалён из распарсенного dict"

    def test_corrupted_hash_returns_none(self, telegram_init_data_factory, test_bot_token):
        from backend.telegram_auth import _verify_init_data, settings

        init_data = telegram_init_data_factory(
            user_id=123,
            bot_token=settings.telegram_bot_token,
            corrupt_hash=True,
        )
        result = _verify_init_data(init_data, settings.telegram_bot_token)
        assert result is None

    def test_missing_hash_returns_none(self, test_bot_token):
        from backend.telegram_auth import _verify_init_data

        # initData без hash
        init_data = "query_id=abc&user=%7B%22id%22%3A1%7D&auth_date=123"
        result = _verify_init_data(init_data, test_bot_token)
        assert result is None

    def test_empty_init_data_returns_none(self, test_bot_token):
        from backend.telegram_auth import _verify_init_data
        assert _verify_init_data("", test_bot_token) is None

    def test_expired_auth_date_returns_none(self, telegram_init_data_factory, test_bot_token):
        """auth_date старше 24 часов → подпись невалидна."""
        from backend.telegram_auth import _verify_init_data, settings

        # 25 часов назад
        expired_ts = int(time.time()) - 25 * 3600
        init_data = telegram_init_data_factory(
            user_id=123,
            auth_date=expired_ts,
            bot_token=settings.telegram_bot_token,
        )
        result = _verify_init_data(init_data, settings.telegram_bot_token)
        assert result is None

    def test_wrong_bot_token_returns_none(self, telegram_init_data_factory, test_bot_token):
        """Подпись с другим bot_token не проходит проверку."""
        from backend.telegram_auth import _verify_init_data, settings

        init_data = telegram_init_data_factory(
            user_id=123,
            bot_token="999999999:wrong-token-xxx",
        )
        result = _verify_init_data(init_data, settings.telegram_bot_token)
        assert result is None


# ============================================================
# _extract_user
# ============================================================
class TestExtractUser:
    def test_valid_user_json(self, telegram_init_data_factory, test_bot_token):
        from backend.telegram_auth import _extract_user, _verify_init_data, settings

        init_data = telegram_init_data_factory(
            user_id=42,
            first_name="Alice",
            last_name="Smith",
            username="alice",
            language_code="en",
            is_premium=True,
            bot_token=settings.telegram_bot_token,
        )
        parsed = _verify_init_data(init_data, settings.telegram_bot_token)
        user = _extract_user(parsed)

        assert user.id == 42
        assert user.first_name == "Alice"
        assert user.last_name == "Smith"
        assert user.username == "alice"
        assert user.language_code == "en"
        assert user.is_premium is True

    def test_missing_user_field_raises_401(self):
        from backend.telegram_auth import _extract_user

        with pytest.raises(HTTPException) as exc_info:
            _extract_user({})  # нет поля "user"

        assert exc_info.value.status_code == 401
        assert "User field missing" in exc_info.value.detail

    def test_invalid_user_json_raises_401(self):
        from backend.telegram_auth import _extract_user

        with pytest.raises(HTTPException) as exc_info:
            _extract_user({"user": "не-json"})

        assert exc_info.value.status_code == 401
        assert "Invalid user JSON" in exc_info.value.detail

    def test_user_with_minimal_fields(self):
        from backend.telegram_auth import _extract_user

        # Только id — остальные берутся из дефолтов
        user_data = {"id": 7}
        parsed = {"user": json.dumps(user_data)}
        user = _extract_user(parsed)

        assert user.id == 7
        assert user.first_name == ""
        assert user.is_premium is False


# ============================================================
# _check_whitelist
# ============================================================
class TestCheckWhitelist:
    def test_empty_whitelist_allows_all(self, monkeypatch, fastapi_test_user):
        from backend.telegram_auth import _check_whitelist, settings

        monkeypatch.setattr(settings, "allowed_user_ids", "")
        # Не должно бросать
        _check_whitelist(fastapi_test_user)

    def test_user_in_whitelist_passes(self, monkeypatch, fastapi_test_user):
        from backend.telegram_auth import _check_whitelist, settings

        monkeypatch.setattr(
            settings, "allowed_user_ids", str(fastapi_test_user.id),
        )
        _check_whitelist(fastapi_test_user)

    def test_user_not_in_whitelist_raises_403(self, monkeypatch, fastapi_test_user):
        from backend.telegram_auth import _check_whitelist, settings

        monkeypatch.setattr(settings, "allowed_user_ids", "111,222,333")
        with pytest.raises(HTTPException) as exc_info:
            _check_whitelist(fastapi_test_user)

        assert exc_info.value.status_code == 403
        assert "not in whitelist" in exc_info.value.detail


# ============================================================
# get_current_user (FastAPI dependency)
# ============================================================
class TestGetCurrentUserDependency:
    @pytest.mark.asyncio
    async def test_valid_init_data_in_query(self, telegram_init_data_factory, test_bot_token):
        from backend.telegram_auth import get_current_user, settings

        init_data = telegram_init_data_factory(
            user_id=555,
            bot_token=settings.telegram_bot_token,
        )
        user = await get_current_user(tg_init_data=init_data, x_tg_init_data=None)
        assert user.id == 555

    @pytest.mark.asyncio
    async def test_valid_init_data_in_header(self, telegram_init_data_factory, test_bot_token):
        from backend.telegram_auth import get_current_user, settings

        init_data = telegram_init_data_factory(
            user_id=777,
            bot_token=settings.telegram_bot_token,
        )
        user = await get_current_user(tg_init_data=None, x_tg_init_data=init_data)
        assert user.id == 777

    @pytest.mark.asyncio
    async def test_missing_init_data_raises_401(self):
        from backend.telegram_auth import get_current_user

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(tg_init_data=None, x_tg_init_data=None)

        assert exc_info.value.status_code == 401
        assert "tg_init_data" in exc_info.value.detail or "X-Tg-Init-Data" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_invalid_signature_raises_401(self, telegram_init_data_factory, test_bot_token):
        from backend.telegram_auth import get_current_user, settings

        init_data = telegram_init_data_factory(
            user_id=1,
            bot_token=settings.telegram_bot_token,
            corrupt_hash=True,
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(tg_init_data=init_data, x_tg_init_data=None)

        assert exc_info.value.status_code == 401
        assert "Invalid Telegram initData signature" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_query_takes_precedence_over_header(
        self, telegram_init_data_factory, test_bot_token,
    ):
        """Если есть и query, и header — используется query."""
        from backend.telegram_auth import get_current_user, settings

        init_data_query = telegram_init_data_factory(
            user_id=111, bot_token=settings.telegram_bot_token,
        )
        init_data_header = telegram_init_data_factory(
            user_id=222, bot_token=settings.telegram_bot_token,
        )
        user = await get_current_user(
            tg_init_data=init_data_query,
            x_tg_init_data=init_data_header,
        )
        assert user.id == 111
