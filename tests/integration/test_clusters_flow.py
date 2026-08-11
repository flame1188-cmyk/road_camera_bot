"""
Wave 3 — Тесты кластеризации, Excel/HTML генерации и точечной статистики.

Цель: покрыть строки 1189-1965 в gibdd_service.py:
  - start_clusters_calculation
  - _serialize_cluster
  - generate_clusters_map_html / _build_clusters_map_html
  - _color_for_severity
  - generate_clusters_excel
  - generate_point_stats_excel
  - generate_point_stats_map_html

Все внешние модули (concentration_points, excel_generator, report_generator,
camera_cache, camera_matcher, point_statistics, bot) подменяются stub'ами.
"""
from __future__ import annotations

import asyncio
import sys
import types
from datetime import datetime, timezone
from typing import Any

import pytest

from tests.integration._gibdd_stubs import install_stubs, make_minimal_cards
from backend.services import _imports  # для патчей _PROJECT_ROOT/_import_module


# ============================================================
# Shared fixtures
# ============================================================
@pytest.fixture
def stub_repository(monkeypatch):
    fake_repo = types.ModuleType("repository")
    async def fake_save_task(t): return None
    async def fake_log_access(**kw): return None
    fake_repo.save_task = fake_save_task
    fake_repo.log_access = fake_log_access
    monkeypatch.setitem(sys.modules, "backend.db.repository", fake_repo)


@pytest.fixture
def stub_db_not_ready(monkeypatch):
    fake_db = types.ModuleType("connection")
    fake_db.is_db_ready = lambda: False
    monkeypatch.setitem(sys.modules, "backend.db.connection", fake_db)


@pytest.fixture
def stub_db_ready(monkeypatch):
    """DB готова — но pool=None (тесты используют monkeypatched clusters_cache,
    который не вызывает реальный pool, так что pool не нужен)."""
    fake_db = types.ModuleType("connection")
    fake_db.is_db_ready = lambda: True
    fake_db.get_pool = lambda: None
    monkeypatch.setitem(sys.modules, "backend.db.connection", fake_db)


@pytest.fixture
def stub_clusters_cache(monkeypatch):
    """Подменяет db.clusters_cache — всегда cache miss."""
    fake_cc = types.ModuleType("clusters_cache")
    async def fake_get(**kw): return None
    async def fake_put(**kw): return None
    fake_cc.get_cached_clusters = fake_get
    fake_cc.put_cached_clusters = fake_put
    monkeypatch.setitem(sys.modules, "backend.db.clusters_cache", fake_cc)


@pytest.fixture
def stub_excel_cache(monkeypatch):
    """Подменяет db.excel_cache — всегда cache miss."""
    fake_ec = types.ModuleType("excel_cache")
    async def fake_get(**kw): return None
    async def fake_put(**kw): return None
    fake_ec.get_cached_excel = fake_get
    fake_ec.put_cached_excel = fake_put
    monkeypatch.setitem(sys.modules, "backend.db.excel_cache", fake_ec)


def _make_concentration_stub(
    clusters: list = None,
    preclusters: list = None,
):
    """Создаёт stub для concentration_points с заданными clusters/preclusters."""
    conc = types.ModuleType("concentration_points")

    clusters = clusters or []
    preclusters = preclusters or []

    async def fake_calculate_concentration_dynamics(
        current_cards, prev_cards, progress_callback=None, reg_code=None,
    ):
        # Вызываем progress_callback для проверки stage updates
        if progress_callback:
            await progress_callback("Test stage 1")
            await progress_callback("Test stage 2")
        return list(clusters), [], list(preclusters)

    def fake_enrich(clusters_list, cameras):
        for c in clusters_list:
            c["camera_match"] = {"count": len(cameras)}

    def fake_build_concentration_excel_data(cs):
        return [{"road": c.get("road", ""), "total": c.get("total_accidents", 0)} for c in cs]
    def fake_get_concentration_column_names():
        return ["road", "total"]
    def fake_build_dynamics_excel_data(cs):
        return [{"road": c.get("road", ""), "status": c.get("dynamics", {}).get("status", "new")} for c in cs]
    def fake_get_dynamics_column_names():
        return ["road", "status"]
    def fake_build_dynamics_detail_data(cs, curr_label, prev_label):
        return []
    def fake_get_dynamics_detail_column_names():
        return ["date"]
    def fake_build_precluster_excel_data(ps):
        return [{"road": p.get("road", "")} for p in ps]
    def fake_get_precluster_column_names():
        return ["road"]

    conc.calculate_concentration_dynamics = fake_calculate_concentration_dynamics
    conc.enrich_clusters_with_cameras = fake_enrich
    conc.build_concentration_excel_data = fake_build_concentration_excel_data
    conc.get_concentration_column_names = fake_get_concentration_column_names
    conc.build_dynamics_excel_data = fake_build_dynamics_excel_data
    conc.get_dynamics_column_names = fake_get_dynamics_column_names
    conc.build_dynamics_detail_data = fake_build_dynamics_detail_data
    conc.get_dynamics_detail_column_names = fake_get_dynamics_detail_column_names
    conc.build_precluster_excel_data = fake_build_precluster_excel_data
    conc.get_precluster_column_names = fake_get_precluster_column_names
    return conc


def _make_excel_generator_clusters_stub():
    """Stub для excel_generator с методами для clusters/point_stats."""
    excel = types.ModuleType("excel_generator")

    def fake_generate_both(file1, file2):
        return b"cards-xlsx", b"participants-xlsx"

    def fake_generate_concentration_dynamics_file(*args):
        return b"clusters-xlsx-bytes"

    def fake_generate_point_stats_file(curr, prev, cols, curr_label, prev_label):
        return b"point-stats-xlsx-bytes"

    excel.generate_both_files = fake_generate_both
    excel.generate_concentration_dynamics_file = fake_generate_concentration_dynamics_file
    excel.generate_point_stats_file = fake_generate_point_stats_file
    return excel


def _make_report_generator_clusters_stub():
    """Stub для report_generator с generate_cluster_map и generate_point_stats_map."""
    rg = types.ModuleType("report_generator")

    class FakeReportGenerator:
        def __init__(self, region_name="", period_label=""):
            self.region_name = region_name
            self.period_label = period_label

        def generate_dtp_map(self, cards, cameras=None, prev_cards=None, prev_label=None):
            return f"<html><body>Fake dtp map: {len(cards)} cards</body></html>"

        def generate_cluster_map(self, clusters, preclusters=None, cameras=None):
            return f"<html><body>Fake cluster map: {len(clusters)} clusters</body></html>"

        def generate_point_stats_map(self, lat, lon, radius_m, cards, prev_cards=None,
                                      cameras=None, curr_label="", prev_label=""):
            return f"<html><body>Fake point map: {lat},{lon} r={radius_m}</body></html>"

    rg.ReportGenerator = FakeReportGenerator
    return rg


def _make_camera_matcher_stub():
    """Stub для camera_matcher — haversine возвращает 100 метров всегда."""
    cm = types.ModuleType("camera_matcher")
    cm.haversine = lambda lat1, lon1, lat2, lon2: 100.0  # 100m
    return cm


def _make_point_statistics_excel_stub():
    """Stub для point_statistics с методами для Excel."""
    ps = types.ModuleType("point_statistics")

    def _build_period(cards):
        return {
            "total": len(cards),
            "deaths": 0,
            "injured": 1,
            "alcohol": 0,
            "pedestrians": 0,
            "by_type": {"Столкновение": len(cards)},
            "by_road": {"Р-5": len(cards)},
            "by_weather": {"Ясно": len(cards)},
            "cards": list(cards),
        }

    def fake_calc(lat, lon, radius_m, cards, prev_cards=None):
        return {
            "current": _build_period(cards),
            "prev": _build_period(prev_cards) if prev_cards else None,
        }

    def fake_build_excel(curr, prev, curr_label, prev_label):
        curr_rows = [{"id": c.get("kart_id", "")} for c in curr]
        prev_rows = [{"id": c.get("kart_id", "")} for c in prev] if prev else []
        return curr_rows, prev_rows

    def fake_get_columns():
        return ["id", "date", "type"]

    ps.calculate_point_statistics = fake_calc
    ps.build_point_stats_excel_data = fake_build_excel
    ps.get_point_stats_column_names = fake_get_columns
    return ps


def _install_clusters_stubs(
    monkeypatch,
    *,
    cards=None,
    prev_cards=None,
    clusters=None,
    preclusters=None,
    has_cameras=False,
    llm_answer="Mock",
):
    """Устанавливает stub'ы для кластерных тестов."""
    if cards is None:
        cards = make_minimal_cards(3)
    stubs = install_stubs(
        monkeypatch, cards=cards, prev_cards=prev_cards,
        llm_answer=llm_answer, has_cameras=has_cameras,
    )
    # Подменяем дополнительные модули для кластеров
    stubs["concentration_points"] = _make_concentration_stub(
        clusters=clusters, preclusters=preclusters,
    )
    stubs["excel_generator"] = _make_excel_generator_clusters_stub()
    stubs["report_generator"] = _make_report_generator_clusters_stub()
    stubs["camera_matcher"] = _make_camera_matcher_stub()
    stubs["point_statistics"] = _make_point_statistics_excel_stub()
    return stubs


def _make_cluster(
    *, road="ул. Мира", total_accidents=5, deaths=0, injured=2,
    center=(59.22, 39.88), dynamics_status="new",
    is_lost=False, is_prev_matched=False,
):
    """Минимальный валидный очаг для тестов."""
    return {
        "road": road,
        "zone_type": "В населенном пункте",
        "total_accidents": total_accidents,
        "deaths": deaths,
        "injured": injured,
        "dominant_type": "Столкновение",
        "type_counter": {"Столкновение": total_accidents},
        "center": center,
        "start_pos": 100.0,
        "end_pos": 500.0,
        "dates": ["15.05.2025"],
        "dynamics": {"status": dynamics_status, "prev_total": 0},
        "cards": [{"kart_id": "1"}],
        "_is_lost": is_lost,
        "_is_prev_matched": is_prev_matched,
    }


# ============================================================
# start_clusters_calculation
# ============================================================
class TestStartClustersCalculation:
    @pytest.mark.asyncio
    async def test_happy_path_with_clusters(
        self, monkeypatch, clear_in_memory_tasks, stub_repository,
        stub_db_not_ready, stub_clusters_cache, stub_excel_cache, tmp_path,
    ):
        """Полный happy path: 2 текущих + 1 lost + 1 precluster."""
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        clusters = [
            _make_cluster(road="ул. А", total_accidents=5, dynamics_status="new"),
            _make_cluster(road="ул. Б", total_accidents=3, dynamics_status="repeated_growing"),
            _make_cluster(road="ул. В (исчез)", total_accidents=2, is_lost=True,
                          dynamics_status="lost"),
        ]
        preclusters = [
            {"road": "предочаг 1", "center": (59.0, 39.0), "total_accidents": 2},
        ]
        _install_clusters_stubs(
            monkeypatch, clusters=clusters, preclusters=preclusters,
            has_cameras=False,
        )

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = make_minimal_cards(3)

        await gibdd_service.start_clusters_calculation(task)

        state = task.clusters_state
        assert state.status == gibdd_service.AnalysisStatus.DONE
        assert state.progress == 100
        assert state.error is None
        assert state.finished_at is not None

        result = state.result
        # 2 текущих (ул. А, ул. Б), 1 lost, 0 prev_matched
        assert result["total_clusters"] == 2
        assert result["total_lost"] == 1
        assert result["total_prev_matched"] == 0
        assert result["total_preclusters"] == 1
        assert result["current_total_dtp"] == 8  # 5 + 3
        assert result["current_deaths"] == 0
        assert result["current_injured"] == 4  # 2 + 2
        assert result["dynamics"]["new"] == 1
        assert result["dynamics"]["repeated_growing"] == 1
        assert result["dynamics"]["lost"] == 1
        assert result["region_name"] == "Рег"
        assert result["current_label"] == "Май 2025"

        # raw_clusters сохранены
        assert len(task.raw_clusters) == 3
        assert len(task.raw_preclusters) == 1

    @pytest.mark.asyncio
    async def test_empty_cards_marks_failed(
        self, monkeypatch, clear_in_memory_tasks, stub_repository,
        stub_db_not_ready, stub_clusters_cache, stub_excel_cache, tmp_path,
    ):
        """Если task.cards пустой и восстановить нельзя — status FAILED.

        Sprint 3.1: ensure_cards пытается восстановить cards из cards_cache.
        Если _fetch_cards_for_period возвращает [] — ensure_cards возвращает
        ok=False, и start_clusters_calculation падает с понятной ошибкой.
        """
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        # stub возвращает пустые cards — ensure_cards не сможет восстановить
        _install_clusters_stubs(monkeypatch, cards=[], clusters=[])

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = []  # пустой

        await gibdd_service.start_clusters_calculation(task)

        state = task.clusters_state
        assert state.status == gibdd_service.AnalysisStatus.FAILED
        # Принимаем оба варианта сообщения — старое и новое (Sprint 3.1)
        assert (
            "не загружены" in state.error
            or "Не удалось восстановить" in state.error
            or "Нет данных" in state.error
        ), f"unexpected error: {state.error}"
        assert state.finished_at is not None

    @pytest.mark.asyncio
    async def test_concentration_module_raises_marks_failed(
        self, monkeypatch, clear_in_memory_tasks, stub_repository,
        stub_db_not_ready, stub_clusters_cache, stub_excel_cache, tmp_path,
    ):
        """Если concentration_points падает — status FAILED."""
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        stubs = _install_clusters_stubs(monkeypatch, clusters=[])

        # Подменяем calculate_concentration_dynamics чтобы падал
        async def failing_calc(*a, **kw):
            raise RuntimeError("OSM Overpass down")
        stubs["concentration_points"].calculate_concentration_dynamics = failing_calc

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = make_minimal_cards(2)

        await gibdd_service.start_clusters_calculation(task)

        state = task.clusters_state
        assert state.status == gibdd_service.AnalysisStatus.FAILED
        assert "OSM Overpass down" in state.error

    @pytest.mark.asyncio
    async def test_with_cameras_enrichment(
        self, monkeypatch, clear_in_memory_tasks, stub_repository,
        stub_db_not_ready, stub_clusters_cache, stub_excel_cache, tmp_path,
    ):
        """Если cameras есть — clusters обогащаются через enrich."""
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        clusters = [_make_cluster(road="ул. А", total_accidents=3)]
        _install_clusters_stubs(monkeypatch, clusters=clusters, has_cameras=True)

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = make_minimal_cards(2)

        await gibdd_service.start_clusters_calculation(task)

        state = task.clusters_state
        assert state.status == gibdd_service.AnalysisStatus.DONE
        # Camera_match должен быть установлен stub'ом enrich
        assert task.raw_clusters[0].get("camera_match") is not None
        assert task.raw_clusters[0]["camera_match"]["count"] == 1


# ============================================================
# Sprint 3.2: clusters_cache recovery (raw_clusters=None → recompute)
# ============================================================
class TestSprint32ClustersCacheRecovery:
    """Sprint 3.2 — фикс простой карты для старых задач.

    Сценарий: пользователь открывает старую задачу (созданную до Stage 4
    fix, когда raw_clusters не сохранялся в clusters_cache). Cache HIT
    возвращает result, но raw_clusters=None. Без Sprint 3.2 карта падает
    в simple map (без слоёв/попапов), Excel возвращает None.

    Фикс: при cache HIT с raw_clusters=None игнорируем кэш и пересчитываем,
    после чего put_cached_clusters сохраняет уже полную запись (с raw).
    """

    @pytest.mark.asyncio
    async def test_cache_hit_without_raw_triggers_recompute(
        self, monkeypatch, clear_in_memory_tasks, stub_repository,
        stub_db_ready, stub_excel_cache, tmp_path,
    ):
        """Cache HIT, но raw_clusters=None → игнор кэша, пересчёт.

        Проверяем, что:
        1. Кэш вернул result с total_clusters=5
        2. Но т.к. raw_clusters=None, идёт пересчёт
        3. После пересчёта: total_clusters=1 (от concentration stub),
           raw_clusters заполнены
        4. put_cached_clusters вызван с новыми raw_clusters
        """
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        # Stub clusters_cache: cache HIT, но БЕЗ raw_clusters (старая запись)
        fake_cc = types.ModuleType("clusters_cache")
        cached_no_raw = {
            "result": {
                "total_clusters": 5,  # в кэше было 5 очагов
                "total_lost": 0, "total_prev_matched": 0,
                "total_preclusters": 0,
                "current_total_dtp": 50, "current_deaths": 1, "current_injured": 10,
                "dynamics": {"new": 5},
                "clusters": [], "preclusters": [],
                "has_prev_data": False, "prev_label": None,
                "current_label": "Май 2025", "region_name": "Рег",
            },
            "raw_clusters": None,  # ← главная причина Sprint 3.2
            "raw_preclusters": None,
        }
        put_calls = []
        async def fake_get(**kw): return cached_no_raw
        async def fake_put(**kw):
            put_calls.append(kw)
            return None
        fake_cc.get_cached_clusters = fake_get
        fake_cc.put_cached_clusters = fake_put
        monkeypatch.setitem(sys.modules, "backend.db.clusters_cache", fake_cc)

        # Stub concentration_points: вернёт 1 очаг (а не 5 из кэша)
        clusters = [_make_cluster(road="ул. Пересчёта", total_accidents=5)]
        _install_clusters_stubs(monkeypatch, clusters=clusters)

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = make_minimal_cards(3)

        await gibdd_service.start_clusters_calculation(task)

        state = task.clusters_state
        assert state.status == gibdd_service.AnalysisStatus.DONE
        # Пересчёт дал 1 очаг (не 5 из кэша)
        assert state.result["total_clusters"] == 1, (
            f"Expected 1 (recomputed), got {state.result['total_clusters']}"
        )
        # raw_clusters заполнены пересчётом
        assert len(task.raw_clusters) == 1
        # PUT вызван с новыми raw_clusters
        assert len(put_calls) == 1
        assert put_calls[0].get("raw_clusters") is not None
        assert len(put_calls[0]["raw_clusters"]) == 1

    @pytest.mark.asyncio
    async def test_cache_hit_with_raw_uses_cache_no_recompute(
        self, monkeypatch, clear_in_memory_tasks, stub_repository,
        stub_db_ready, stub_excel_cache, tmp_path,
    ):
        """Cache HIT с raw_clusters → кэш используется, без пересчёта.

        Это проверка, что Sprint 3.2 фикс не сломал штатный cache hit.
        concentration_points НЕ должен вызываться.
        """
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        # Stub clusters_cache: cache HIT С raw_clusters (новая запись)
        fake_cc = types.ModuleType("clusters_cache")
        cached_full = {
            "result": {
                "total_clusters": 3,  # в кэше 3 очага
                "total_lost": 0, "total_prev_matched": 0,
                "total_preclusters": 0,
                "current_total_dtp": 30, "current_deaths": 0, "current_injured": 5,
                "dynamics": {"new": 3},
                "clusters": [], "preclusters": [],
                "has_prev_data": False, "prev_label": None,
                "current_label": "Май 2025", "region_name": "Рег",
            },
            "raw_clusters": [{"road": "ул. ИзКэша", "cards": []}],  # есть raw!
            "raw_preclusters": None,
        }
        async def fake_get(**kw): return cached_full
        async def fake_put(**kw): return None
        fake_cc.get_cached_clusters = fake_get
        fake_cc.put_cached_clusters = fake_put
        monkeypatch.setitem(sys.modules, "backend.db.clusters_cache", fake_cc)

        # Stub concentration_points — должен НЕ вызываться
        conc_called = []
        clusters = [_make_cluster(road="ул. НЕдолжноБыть", total_accidents=99)]
        stubs = _install_clusters_stubs(monkeypatch, clusters=clusters)
        # Перехватываем calculate_concentration_dynamics
        original_calc = stubs["concentration_points"].calculate_concentration_dynamics
        async def spy_calc(*a, **kw):
            conc_called.append(True)
            return await original_calc(*a, **kw)
        stubs["concentration_points"].calculate_concentration_dynamics = spy_calc

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = make_minimal_cards(3)

        await gibdd_service.start_clusters_calculation(task)

        state = task.clusters_state
        assert state.status == gibdd_service.AnalysisStatus.DONE
        # Кэш вернул 3 очага (не пересчитано в 1)
        assert state.result["total_clusters"] == 3
        # raw_clusters восстановлены из кэша
        assert len(task.raw_clusters) == 1
        assert task.raw_clusters[0]["road"] == "ул. ИзКэша"
        # concentration_points НЕ вызывался
        assert conc_called == [], (
            f"concentration_points called {len(conc_called)} times — "
            f"cache should have been used"
        )

    @pytest.mark.asyncio
    async def test_cache_miss_proceeds_normal_calculation(
        self, monkeypatch, clear_in_memory_tasks, stub_repository,
        stub_db_ready, stub_excel_cache, tmp_path,
    ):
        """Cache MISS → штатный расчёт через concentration_points.

        Регрессионный тест: Sprint 3.2 фикс не должен ломать cache miss.
        """
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        # Stub clusters_cache: cache MISS (возвращает None)
        fake_cc = types.ModuleType("clusters_cache")
        async def fake_get(**kw): return None
        async def fake_put(**kw): return None
        fake_cc.get_cached_clusters = fake_get
        fake_cc.put_cached_clusters = fake_put
        monkeypatch.setitem(sys.modules, "backend.db.clusters_cache", fake_cc)

        clusters = [_make_cluster(road="ул. Новая", total_accidents=3)]
        _install_clusters_stubs(monkeypatch, clusters=clusters)

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = make_minimal_cards(2)

        await gibdd_service.start_clusters_calculation(task)

        state = task.clusters_state
        assert state.status == gibdd_service.AnalysisStatus.DONE
        assert state.result["total_clusters"] == 1
        assert len(task.raw_clusters) == 1
        assert task.raw_clusters[0]["road"] == "ул. Новая"


# ============================================================
# _serialize_cluster
# ============================================================
class TestSerializeCluster:
    def test_serializes_basic_cluster(self):
        from backend.services import gibdd_service

        c = _make_cluster(road="ул. Мира", total_accidents=5, deaths=1, injured=2)
        result = gibdd_service._serialize_cluster(c)

        assert result["road"] == "ул. Мира"
        assert result["total_accidents"] == 5
        assert result["deaths"] == 1
        assert result["injured"] == 2
        assert result["center"] == {"lat": 59.22, "lon": 39.88}
        assert result["dominant_type"] == "Столкновение"
        assert result["is_lost"] is False
        assert result["is_prev_matched"] is False

    def test_serializes_with_none_dominant_type(self):
        """dominant_type=None (смешанный тип) → пустая строка для UI."""
        from backend.services import gibdd_service

        c = _make_cluster()
        c["dominant_type"] = None
        result = gibdd_service._serialize_cluster(c)
        assert result["dominant_type"] == ""

    def test_serializes_with_none_center(self):
        """center=None → center=None в результате."""
        from backend.services import gibdd_service

        c = _make_cluster()
        c["center"] = None
        result = gibdd_service._serialize_cluster(c)
        assert result["center"] is None

    def test_serializes_lost_cluster(self):
        from backend.services import gibdd_service

        c = _make_cluster(is_lost=True, dynamics_status="lost")
        result = gibdd_service._serialize_cluster(c)
        assert result["is_lost"] is True


# ============================================================
# generate_clusters_map_html
# ============================================================
class TestGenerateClustersMapHtml:
    @pytest.mark.asyncio
    async def test_happy_path(
        self, monkeypatch, clear_in_memory_tasks, stub_db_not_ready,
        stub_repository, stub_clusters_cache, stub_excel_cache, tmp_path,
    ):
        """generate_clusters_map_html возвращает HTML от ReportGenerator."""
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        clusters = [_make_cluster(road="ул. А"), _make_cluster(road="ул. Б")]
        _install_clusters_stubs(monkeypatch, clusters=clusters)

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = make_minimal_cards(2)
        # Запускаем расчёт очагов
        await gibdd_service.start_clusters_calculation(task)

        html = await gibdd_service.generate_clusters_map_html(task)
        assert html is not None
        assert "Fake cluster map" in html
        assert "2 clusters" in html

    @pytest.mark.asyncio
    async def test_no_result_returns_none(
        self, monkeypatch, clear_in_memory_tasks, stub_db_not_ready,
    ):
        """Если clusters_state.result=None → generate_clusters_map_html возвращает None."""
        from backend.services import gibdd_service

        _install_clusters_stubs(monkeypatch, clusters=[])

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        # clusters_state пустой (не запускали)

        html = await gibdd_service.generate_clusters_map_html(task)
        assert html is None

    @pytest.mark.asyncio
    async def test_empty_raw_falls_back_to_simple_map(
        self, monkeypatch, clear_in_memory_tasks, stub_db_not_ready,
        stub_repository, stub_clusters_cache, stub_excel_cache, tmp_path,
    ):
        """raw_clusters=[] и raw_preclusters=[] → fallback на _build_clusters_map_html."""
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        _install_clusters_stubs(monkeypatch, clusters=[], preclusters=[])

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = make_minimal_cards(1)
        await gibdd_service.start_clusters_calculation(task)

        html = await gibdd_service.generate_clusters_map_html(task)
        # Должна отработать _build_clusters_map_html (простая Leaflet-карта)
        assert html is not None
        assert "<html>" in html or "leaflet" in html.lower() or "<body>" in html

    @pytest.mark.asyncio
    async def test_with_lost_clusters_adds_banner(
        self, monkeypatch, clear_in_memory_tasks, stub_db_not_ready,
        stub_repository, stub_clusters_cache, stub_excel_cache, tmp_path,
    ):
        """Если есть lost clusters — к HTML добавляется плашка 'Исчезнувшие очаги'."""
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        clusters = [
            _make_cluster(road="ул. А"),
            _make_cluster(road="исчез", is_lost=True, dynamics_status="lost"),
        ]
        _install_clusters_stubs(monkeypatch, clusters=clusters)

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = make_minimal_cards(2)
        await gibdd_service.start_clusters_calculation(task)

        html = await gibdd_service.generate_clusters_map_html(task)
        assert html is not None
        assert "Исчезнувшие очаги" in html


# ============================================================
# generate_clusters_excel
# ============================================================
class TestGenerateClustersExcel:
    @pytest.mark.asyncio
    async def test_happy_path(
        self, monkeypatch, clear_in_memory_tasks, stub_db_not_ready,
        stub_repository, stub_clusters_cache, stub_excel_cache, tmp_path,
    ):
        """generate_clusters_excel возвращает bytes."""
        from backend.services import gibdd_service

        monkeypatch.setattr(gibdd_service, "_PROJECT_ROOT", tmp_path)

        monkeypatch.setattr(_imports, "_PROJECT_ROOT", tmp_path)

        clusters = [_make_cluster(road="ул. А"), _make_cluster(road="ул. Б")]
        preclusters = [{"road": "precluster", "center": (59.0, 39.0)}]
        _install_clusters_stubs(monkeypatch, clusters=clusters, preclusters=preclusters)

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = make_minimal_cards(2)
        await gibdd_service.start_clusters_calculation(task)

        xlsx = await gibdd_service.generate_clusters_excel(task)
        assert xlsx is not None
        assert isinstance(xlsx, bytes)
        assert xlsx == b"clusters-xlsx-bytes"

    @pytest.mark.asyncio
    async def test_no_raw_returns_none(
        self, monkeypatch, clear_in_memory_tasks, stub_db_not_ready,
    ):
        """raw_clusters=[] и raw_preclusters=[] → возвращает None."""
        from backend.services import gibdd_service

        _install_clusters_stubs(monkeypatch, clusters=[], preclusters=[])

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        # Ничего не считали — raw пустые

        result = await gibdd_service.generate_clusters_excel(task)
        assert result is None


# ============================================================
# generate_point_stats_excel
# ============================================================
class TestGeneratePointStatsExcel:
    @pytest.mark.asyncio
    async def test_happy_path(
        self, monkeypatch, clear_in_memory_tasks, stub_db_not_ready,
    ):
        """generate_point_stats_excel возвращает bytes."""
        from backend.services import gibdd_service

        _install_clusters_stubs(monkeypatch)

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        # Имитируем, что point_stats уже считали
        task.last_point_cards_current = make_minimal_cards(3)
        task.last_point_cards_prev = make_minimal_cards(2)

        xlsx = await gibdd_service.generate_point_stats_excel(task)
        assert xlsx is not None
        assert isinstance(xlsx, bytes)
        assert xlsx == b"point-stats-xlsx-bytes"

    @pytest.mark.asyncio
    async def test_no_point_cards_returns_none(
        self, monkeypatch, clear_in_memory_tasks, stub_db_not_ready,
    ):
        """Если last_point_cards_current/prev пустые → None."""
        from backend.services import gibdd_service

        _install_clusters_stubs(monkeypatch)

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        # last_point_cards_* пустые

        result = await gibdd_service.generate_point_stats_excel(task)
        assert result is None


# ============================================================
# generate_point_stats_map_html
# ============================================================
class TestGeneratePointStatsMapHtml:
    @pytest.mark.asyncio
    async def test_happy_path(
        self, monkeypatch, clear_in_memory_tasks, stub_db_not_ready,
    ):
        """generate_point_stats_map_html возвращает HTML."""
        from backend.services import gibdd_service

        _install_clusters_stubs(monkeypatch, has_cameras=True)

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        task.cards = make_minimal_cards(3)

        html = await gibdd_service.generate_point_stats_map_html(
            task, lat=59.22, lon=39.88, radius_m=500,
        )
        assert html is not None
        assert "Fake point map" in html
        assert "59.22" in html

    @pytest.mark.asyncio
    async def test_empty_cards_returns_none(
        self, monkeypatch, clear_in_memory_tasks, stub_db_not_ready,
    ):
        """Если task.cards пустой → None."""
        from backend.services import gibdd_service

        _install_clusters_stubs(monkeypatch)

        task = gibdd_service.create_task(
            user_id=1, region_code="1101", region_name="Рег",
            period_label="Май 2025", dat_list=["5.2025"], raw_query="q",
        )
        # cards пустой

        result = await gibdd_service.generate_point_stats_map_html(
            task, lat=59.22, lon=39.88, radius_m=500,
        )
        assert result is None


# ============================================================
# _color_for_severity
# ============================================================
class TestColorForSeverity:
    def test_zero_deaths(self):
        from backend.services import gibdd_service
        c = _make_cluster(deaths=0)
        color = gibdd_service._color_for_severity(c)
        assert isinstance(color, str)
        assert color.startswith("#")

    def test_with_deaths(self):
        from backend.services import gibdd_service
        c = _make_cluster(deaths=3)
        color = gibdd_service._color_for_severity(c)
        assert isinstance(color, str)
        assert color.startswith("#")
