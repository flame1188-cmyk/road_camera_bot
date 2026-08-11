"""
Wave 3 — shared helpers for gibdd_service integration tests.

Содержит фабрику stub-модулей, которые подменяют bot / gibdd_parser /
analytics / excel_generator / report_generator / llm_analyzer /
point_statistics внутри gibdd_service._import_module.

Цель: тестировать полный пайплайн execute_task без реальной сети ГИБДД,
без реальной генерации Excel (медленно) и без вызова LLM.

Использование:

    from tests.integration._gibdd_stubs import (
        install_stubs,
        make_minimal_cards,
        BotStubConfig,
    )

    def test_x(monkeypatch, clear_in_memory_tasks):
        install_stubs(monkeypatch, bot_cards=make_minimal_cards())
        ... вызываем execute_task ...
"""
from __future__ import annotations

import types
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest


# ============================================================
# Карточки ДТП — минимальный валидный набор
# ============================================================
def make_minimal_cards(n: int = 3) -> List[Dict[str, Any]]:
    """Возвращает n валидных карточек для пайплайна.

    Карточки минимально достаточны для gibdd_parser.build_file1_data,
    analytics.calculate_metrics, analytics.build_full_analytics.
    """
    cards: List[Dict[str, Any]] = []
    for i in range(n):
        cards.append({
            "kart_id": f"0000{i+1:02d}",
            "date_dtp": f"15.0{(i % 9) + 1}.2025",
            "time": "14:30",
            "coord_w": "59.22",
            "coord_l": "39.88",
            "dtpv": "Столкновение",
            "k_ts": "2",
            "k_uch": "2",
            "pog": "0" if i % 2 == 0 else "1",
            "ran": "1" if i % 2 == 0 else "0",
            "s_dtp": "1",
            "district": "Центральный",
            "house": str(10 + i),
            "km": "",
            "m": "",
            "np": "Вологда",
            "street": "ул. Мира",
            "dor": "Р-5",
            "dor_z": "Федерального значения",
            "dor_k": "IA",
            "k_ul": "Магистральная улица",
            "dor_usl": {
                "s_pch": "Сухое",
                "osv": "В светлое время суток",
                "chom": "Не изменился",
                "sdor": [],
                "obj_dtp": [],
                "ndu": [],
                "factor": [],
                "spog": ["Ясно"],
            },
            "ts_info": [{
                "n_ts": "1",
                "t_ts": "Легковой автомобиль",
                "marka_ts": "LADA",
                "m_ts": "Vesta",
                "color": "Серебристый",
                "g_v": "2022",
                "o_pf": "Собственность гражданина",
                "ts_uch": [{
                    "n_uch": "1",
                    "kt_uch": "Водитель",
                    "pol": "Мужчина",
                    "s_t": "Ранен",
                    "alco": "0",
                    "v_st": "5",
                    "safety_belt": "Пристегнут",
                    "s_seat_group": "",
                    "npdd": ["Превышение скорости"],
                    "sop_npdd": [],
                    "s_sm": "Нет",
                }],
            }],
            "uch_info": [],
        })
    return cards


# ============================================================
# Сборка stub-модулей
# ============================================================
class BotStubConfig:
    """Конфигурация для stub'а bot._fetch_cards_for_period.

    Атрибуты:
        cards: что возвращать при текущем периоде
        prev_cards: что возвращать при прошлом периоде (год назад)
        errors: список ошибок (пустой = успех)
        raise_exc: исключение, которое поднимает fetch (тестируем error path)
        record_calls: список для записи аргументов вызовов (для assertions)
    """
    def __init__(
        self,
        cards: Optional[List[Dict[str, Any]]] = None,
        prev_cards: Optional[List[Dict[str, Any]]] = None,
        errors: Optional[List[str]] = None,
        raise_exc: Optional[Exception] = None,
        record_calls: Optional[List[Dict[str, Any]]] = None,
    ):
        self.cards = cards if cards is not None else make_minimal_cards()
        self.prev_cards = prev_cards if prev_cards is not None else []
        self.errors = errors or []
        self.raise_exc = raise_exc
        self.record_calls = record_calls


def _make_bot_stub(cfg: BotStubConfig) -> types.ModuleType:
    """Создаёт stub-модуль bot с подменой _fetch_cards_for_period."""
    bot = types.ModuleType("bot")

    async def fake_fetch(*, dat_list, reg_code, log_prefix, cache_result):
        if cfg.record_calls is not None:
            cfg.record_calls.append({
                "dat_list": list(dat_list),
                "reg_code": reg_code,
                "log_prefix": log_prefix,
            })
        if cfg.raise_exc is not None:
            raise cfg.raise_exc
        # Эвристика: если все годы в dat_list < текущего года — это prev
        # Берём максимальный год в dat_list и сравниваем
        max_year = 0
        for d in dat_list:
            try:
                _, y = d.split(".")
                max_year = max(max_year, int(y))
            except Exception:
                pass
        # Если в dat_list год < 2025 — считаем это прошлым периодом
        if max_year < 2025:
            return list(cfg.prev_cards), list(cfg.errors)
        return list(cfg.cards), list(cfg.errors)

    bot._fetch_cards_for_period = fake_fetch
    return bot


def _make_gibdd_parser_stub() -> types.ModuleType:
    """Stub для gibdd_parser — возвращает минимальные File1/File2."""
    parser = types.ModuleType("gibdd_parser")

    def fake_build_file1(cards):
        return {"columns": ["kart_id", "date_dtp"], "rows": [[c.get("kart_id"), c.get("date_dtp")] for c in cards]}

    def fake_build_file2(cards):
        return {"columns": ["n_uch", "pol"], "rows": []}

    parser.build_file1_data = fake_build_file1
    parser.build_file2_data = fake_build_file2
    return parser


def _make_analytics_stub() -> types.ModuleType:
    """Stub для analytics — возвращает минимальные метрики."""
    analytics = types.ModuleType("analytics")

    def fake_calculate_metrics(cards):
        return {
            "total": len(cards),
            "deaths": sum(int(c.get("pog", 0) or 0) for c in cards),
            "injured": sum(int(c.get("ran", 0) or 0) for c in cards),
            "alcohol": 0,
            "pedestrians": 0,
            "deaths_per_100": 5.0,
            "injured_per_100": 120.0,
            "by_weekday": {"Пн": 1, "Вт": 2},
            "by_hour": {12: 1, 14: 2},
            "by_type": {"Столкновение": len(cards)},
            "by_weather": {"Ясно": len(cards)},
        }

    def fake_compare_metrics(current, prev):
        return {
            "total": {"current": current.get("total", 0), "previous": prev.get("total", 0) if prev else 0,
                      "change": 25.0 if prev else 0},
            "deaths": {"current": current.get("deaths", 0), "previous": prev.get("deaths", 0) if prev else 0,
                       "change": -50.0 if prev else 0},
            "injured": {"current": current.get("injured", 0), "previous": prev.get("injured", 0) if prev else 0,
                        "change": 20.0 if prev else 0},
            "alcohol": {"current": 0, "previous": 0, "change": 0},
            "pedestrians": {"current": 0, "previous": 0, "change": 0},
            "deaths_per_100": {"current": 5.0, "previous": 10.0 if prev else 0, "change": -50.0 if prev else 0},
            "injured_per_100": {"current": 120.0, "previous": 125.0 if prev else 0, "change": -4.0 if prev else 0},
            "by_weekday": {"current": current.get("by_weekday", {}), "previous": prev.get("by_weekday", {}) if prev else {}},
            "by_hour": {"current": current.get("by_hour", {}), "previous": prev.get("by_hour", {}) if prev else {}},
            "by_type": {"current": current.get("by_type", {}), "previous": prev.get("by_type", {}) if prev else {}},
            "by_weather": {"current": current.get("by_weather", {}), "previous": prev.get("by_weather", {}) if prev else {}},
        }

    def fake_calculate_cross_tables(cards):
        return {
            "dtp_type_x_severity": {"Столкновение": {"Лёгкий": len(cards)}},
            "alcohol_x_weekday": {},
            "month_x_severity": {},
        }

    def fake_calculate_statistical_metrics(cross_tables):
        return {"severity_rate": 0.5, "z_score": 1.2}

    def fake_build_full_analytics(cards, prev_cards=None, prev_label=None):
        current = fake_calculate_metrics(cards)
        prev = fake_calculate_metrics(prev_cards) if prev_cards else None
        return {
            "current_metrics": current,
            "prev_metrics": prev,
            "comparison": fake_compare_metrics(current, prev) if prev else None,
            "has_prev_data": bool(prev_cards),
            "prev_label": prev_label,
            "current_label": "Test period",
            "cross_tables": fake_calculate_cross_tables(cards),
        }

    analytics.calculate_metrics = fake_calculate_metrics
    analytics.compare_metrics = fake_compare_metrics
    analytics.calculate_cross_tables = fake_calculate_cross_tables
    analytics.calculate_statistical_metrics = fake_calculate_statistical_metrics
    analytics.build_full_analytics = fake_build_full_analytics
    return analytics


def _make_excel_generator_stub() -> types.ModuleType:
    """Stub для excel_generator — возвращает минимальные байты."""
    excel_gen = types.ModuleType("excel_generator")

    def fake_generate_both(file1_data, file2_data):
        return b"fake-excel-cards-bytes", b"fake-excel-participants-bytes"

    excel_gen.generate_both_files = fake_generate_both
    return excel_gen


def _make_report_generator_stub() -> types.ModuleType:
    """Stub для report_generator — возвращает минимальный HTML."""
    report_gen = types.ModuleType("report_generator")

    class FakeReportGenerator:
        def __init__(self, region_name="", period_label=""):
            self.region_name = region_name
            self.period_label = period_label

        def generate_dtp_map(self, cards, cameras=None, prev_cards=None, prev_label=None):
            return f"<html><body>Fake map for {len(cards)} cards in {self.region_name}</body></html>"

    report_gen.ReportGenerator = FakeReportGenerator
    return report_gen


def _make_llm_analyzer_stub(answer_text: str = "Mock LLM summary") -> types.ModuleType:
    """Stub для llm_analyzer — возвращает предсказуемый текст.
    Включая Sprint 4 streaming-функции (get_ai_summary_stream,
    get_ai_answer_stream), которые возвращают async generator,
    yield'ящий ответ одним chunk'ом.
    """
    llm = types.ModuleType("llm_analyzer")

    async def fake_get_ai_summary(**kwargs):
        return answer_text

    async def fake_get_ai_answer(**kwargs):
        return answer_text

    # Sprint 4: streaming stubs.
    # В тестах мы обычно НЕ хотим ходить в реальный LLM — стрим-генератор
    # просто yield'ит answer_text одним куском. Тесты, которым нужен
    # реальный SSE-парсинг, используют respx на уровне HTTP (мокают
    # httpx-клиент, а не модуль).
    async def fake_get_ai_summary_stream(**kwargs):
        yield answer_text

    async def fake_get_ai_answer_stream(**kwargs):
        yield answer_text

    def fake_format_clusters_for_prompt(clusters, max_clusters=10):
        return "Mock clusters context"

    def fake_format_cross_tables_for_prompt(current, prev, current_label, prev_label):
        return "Mock cross tables context"

    def fake_format_statistical_metrics_for_prompt(stats):
        return "Mock statistical metrics"

    llm.get_ai_summary = fake_get_ai_summary
    llm.get_ai_answer = fake_get_ai_answer
    llm.get_ai_summary_stream = fake_get_ai_summary_stream
    llm.get_ai_answer_stream = fake_get_ai_answer_stream
    llm.format_clusters_for_prompt = fake_format_clusters_for_prompt
    llm.format_cross_tables_for_prompt = fake_format_cross_tables_for_prompt
    llm.format_statistical_metrics_for_prompt = fake_format_statistical_metrics_for_prompt
    llm.SYSTEM_PROMPT = "Mock system prompt"
    llm.SYSTEM_PROMPT_PAID = "Mock paid system prompt"
    return llm


def _make_point_statistics_stub() -> types.ModuleType:
    """Stub для point_statistics — возвращает минимальные stats.

    Если prev_cards передан — возвращает prev-секцию с подсчётом.
    Это соответствует контракту реального point_statistics.
    """
    ps = types.ModuleType("point_statistics")

    def _build_period(cards):
        return {
            "total": len(cards),
            "deaths": sum(int(c.get("pog", 0) or 0) for c in cards),
            "injured": sum(int(c.get("ran", 0) or 0) for c in cards),
            "alcohol": 0,
            "pedestrians": 0,
            "by_type": {"Столкновение": len(cards)},
            "by_road": {"Р-5": len(cards)},
            "by_weather": {"Ясно": len(cards)},
            "cards": list(cards),
        }

    def fake_calculate_point_statistics(lat, lon, radius_m, cards, prev_cards=None):
        return {
            "current": _build_period(cards),
            "prev": _build_period(prev_cards) if prev_cards else None,
        }

    ps.calculate_point_statistics = fake_calculate_point_statistics
    return ps


def _make_camera_cache_stub(has_cameras: bool = False) -> types.ModuleType:
    """Stub для camera_cache."""
    cc = types.ModuleType("camera_cache")

    def fake_has_cached(reg_code):
        return has_cameras

    def fake_load(reg_code):
        return [] if not has_cameras else [{"lat": 59.22, "lon": 39.88, "has_piket": True}]

    cc.has_cached_cameras = fake_has_cached
    cc.load_cameras_from_cache = fake_load
    return cc


def _make_config_stub(
    *,
    llm_api_key: str = "test-free-key",
    llm_paid_api_key: str = "test-paid-key",
    llm_paid_api_url: str = "https://test.example.com/v1",
    llm_model: str = "test-glm-flash",
    llm_paid_model: str = "test-deepseek",
) -> types.ModuleType:
    """Stub для config — с тестовыми LLM-ключами."""
    cfg = types.ModuleType("config")
    cfg.LLM_API_KEY = llm_api_key
    cfg.LLM_MODEL = llm_model
    cfg.LLM_PAID_API_KEY = llm_paid_api_key
    cfg.LLM_PAID_API_URL = llm_paid_api_url
    cfg.LLM_PAID_MODEL = llm_paid_model
    cfg.LLM_MAX_TOKENS = 16384
    cfg.TELEGRAM_BOT_TOKEN = "test:token"
    cfg.REGIONS_API_ENABLED = 0
    return cfg


# ============================================================
# Главная функция — устанавливает все stub'ы
# ============================================================
def install_stubs(
    monkeypatch,
    *,
    bot_cfg: Optional[BotStubConfig] = None,
    cards: Optional[List[Dict[str, Any]]] = None,
    prev_cards: Optional[List[Dict[str, Any]]] = None,
    bot_errors: Optional[List[str]] = None,
    bot_raise: Optional[Exception] = None,
    llm_answer: str = "Mock LLM summary",
    has_cameras: bool = False,
    config_overrides: Optional[Dict[str, Any]] = None,
    record_bot_calls: Optional[List[Dict[str, Any]]] = None,
):
    """Устанавливает stub-модули в gibdd_service._import_module.

    Параметры:
        bot_cfg: полная конфигурация bot stub (приоритет над cards/prev_cards/errors)
        cards: карточки для текущего периода (если bot_cfg не задан)
        prev_cards: карточки для прошлого периода
        bot_errors: ошибки выгрузки
        bot_raise: исключение, которое поднимает _fetch_cards_for_period
        llm_answer: текст, который возвращает LLM
        has_cameras: есть ли камеры в кэше
        config_overrides: переопределения для config stub
        record_bot_calls: список для записи аргументов вызовов bot._fetch

    Возвращает словарь со ссылками на установленные stub-модули (для assertions).
    """
    if bot_cfg is None:
        bot_cfg = BotStubConfig(
            cards=cards,
            prev_cards=prev_cards,
            errors=bot_errors,
            raise_exc=bot_raise,
            record_calls=record_bot_calls,
        )

    from backend.services import gibdd_service

    config_kwargs = {
        "llm_api_key": "test-free-key",
        "llm_paid_api_key": "test-paid-key",
        "llm_paid_api_url": "https://test.example.com/v1",
    }
    if config_overrides:
        config_kwargs.update(config_overrides)

    stubs = {
        "bot": _make_bot_stub(bot_cfg),
        "gibdd_parser": _make_gibdd_parser_stub(),
        "analytics": _make_analytics_stub(),
        "excel_generator": _make_excel_generator_stub(),
        "report_generator": _make_report_generator_stub(),
        "llm_analyzer": _make_llm_analyzer_stub(llm_answer),
        "point_statistics": _make_point_statistics_stub(),
        "camera_cache": _make_camera_cache_stub(has_cameras),
        "config": _make_config_stub(**config_kwargs),
    }

    def smart_import(name: str):
        if name in stubs:
            return stubs[name]
        # Реальный импорт для прочих модулей (например, regions_builtin)
        return __import__(name)

    # Патчим _import_module в _imports (источник) — все service-модули
    # (pipeline, clusters_ops, llm_ops, ...) используют _imports._import_module()
    # через атрибут модуля, поэтому патч в _imports виден всем.
    from backend.services import _imports as _imp_mod
    monkeypatch.setattr(_imp_mod, "_import_module", smart_import)
    # Дублируем патч в facade для обратной совместимости
    # (на случай если какой-то код вызывает gibdd_service._import_module напрямую)
    monkeypatch.setattr(gibdd_service, "_import_module", smart_import)
    return stubs


# ============================================================
# Pytest fixture-обёртка для удобства
# ============================================================
@pytest.fixture
def gibdd_stubs(monkeypatch, clear_in_memory_tasks):
    """Устанавливает stub-модули и возвращает dict для управления конфигурацией.

    Использование:
        def test_x(gibdd_stubs):
            stubs = gibdd_stubs.install()  # устанавливает дефолтные stub'ы
            ... # тестируем

    Можно переопределить конфигурацию:
        stubs = gibdd_stubs.install(cards=[...], bot_raise=RuntimeError("net"))
    """
    class _GibddStubsHelper:
        def __init__(self):
            self.last_stubs: Optional[Dict[str, Any]] = None
            self.bot_cfg: Optional[BotStubConfig] = None

        def install(self, **kwargs) -> Dict[str, Any]:
            self.last_stubs = install_stubs(monkeypatch, **kwargs)
            return self.last_stubs

        def set_bot(self, **bot_kwargs) -> BotStubConfig:
            """Обновляет конфигурацию bot stub (перe-устанавливает все stub'ы)."""
            cfg = BotStubConfig(**bot_kwargs)
            self.bot_cfg = cfg
            self.last_stubs = install_stubs(monkeypatch, bot_cfg=cfg)
            return cfg

    return _GibddStubsHelper()
