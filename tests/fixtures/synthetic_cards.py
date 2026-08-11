"""
Синтетические карточки ДТП для тестов.

Реальная карточка ГИБДД содержит ~50 полей и вложенные объекты.
Здесь — минимальный набор полей, достаточный для analytics.py
и gibdd_parser.py. Тесты не зависят от конкретных значений —
можно безопасно расширять.

Формат полей соответствует ответу API stat.gibdd.ru
(см. gibdd_parser.parse_card_to_row для маппинга).
"""
import copy
from typing import Any


# Базовая карточка — ДТП без погибших, без нетрезвых, без пешеходов.
# Используется как заготовка, из которой собираем специфичные кейсы.
BASE_CARD: dict[str, Any] = {
    "kart_id": "000001",
    "date_dtp": "15.05.2025",   # Четверг
    "time": "14:30",
    "coord_w": "59.22",
    "coord_l": "39.88",
    "dtpv": "Столкновение",
    "k_ts": "2",
    "k_uch": "3",
    "pog": "0",
    "ran": "1",
    "s_dtp": "1",
    "district": "Центральный",
    "house": "10",
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
    "ts_info": [
        {
            "n_ts": "1",
            "t_ts": "Легковой автомобиль",
            "marka_ts": "LADA",
            "m_ts": "Vesta",
            "color": "Серебристый",
            "g_v": "2022",
            "o_pf": "Собственность гражданина",
            "ts_uch": [
                {
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
                }
            ],
        }
    ],
    "uch_info": [],  # Без пешеходов
}


def make_card(**overrides: Any) -> dict[str, Any]:
    """Возвращает копию BASE_CARD с переопределёнными полями.

    Глубокое копирование — чтобы изменения в одном тесте не влияли на другие.
    Поддерживает вложенные поля через dot-нотацию НЕ — только верхний уровень.
    Для вложенных полей передавайте готовый объект целиком:
        make_card(dor_usl={**BASE_CARD["dor_usl"], "spog": ["Дождь"]})
    """
    card = copy.deepcopy(BASE_CARD)
    card.update(overrides)
    return card


# --- Готовые специфичные карточки ---

def card_with_death() -> dict[str, Any]:
    """ДТП с одним погибшим."""
    return make_card(
        kart_id="000002",
        date_dtp="16.05.2025",  # Пятница
        pog="1",
        ran="0",
        ts_info=[{
            **BASE_CARD["ts_info"][0],
            "ts_uch": [{
                **BASE_CARD["ts_info"][0]["ts_uch"][0],
                "s_t": "Погиб",
            }],
        }],
    )


def card_with_alcohol() -> dict[str, Any]:
    """ДТП с нетрезвым водителем (alco != 0)."""
    return make_card(
        kart_id="000003",
        date_dtp="17.05.2025",  # Суббота
        time="23:45",
        ts_info=[{
            **BASE_CARD["ts_info"][0],
            "ts_uch": [{
                **BASE_CARD["ts_info"][0]["ts_uch"][0],
                "alco": "Установлено опьянение",
            }],
        }],
    )


def card_with_pedestrian() -> dict[str, Any]:
    """ДТП с пешеходом — пешеход указан в uch_info."""
    return make_card(
        kart_id="000004",
        date_dtp="18.05.2025",  # Воскресенье
        time="08:15",
        dtpv="Наезд на пешехода",
        ran="2",
        uch_info=[{
            "n_uch": "2",
            "kt_uch": "Пешеход",
            "pol": "Женщина",
            "s_t": "Ранен",
            "alco": "0",
            "npdd": [],
            "sop_npdd": [],
            "s_sm": "Нет",
        }],
    )


def card_unknown_type() -> dict[str, Any]:
    """ДТП с необычным видом — должно попасть в «Иные ДТП»."""
    return make_card(
        kart_id="000005",
        dtpv="Иной вид ДТП",
    )


def card_empty_time() -> dict[str, Any]:
    """Карточка без времени — не должна учитываться в by_hour."""
    return make_card(
        kart_id="000006",
        time="",
    )


def card_invalid_date() -> dict[str, Any]:
    """Карточка с битой датой — не должна учитываться в by_weekday."""
    return make_card(
        kart_id="000007",
        date_dtp="не-дата",
    )


def card_municipal_road() -> dict[str, Any]:
    """ДТП на муниципальной дороге."""
    return make_card(
        kart_id="000008",
        dor="ул. Ленина",
        dor_z="Муниципального значения",
    )


def cards_basic_set() -> list[dict[str, Any]]:
    """Набор из 5 карточек: базовое + 4 специфичных.

    Используется в большинстве тестов calculate_metrics / calculate_cross_tables.
    """
    return [
        BASE_CARD,
        card_with_death(),
        card_with_alcohol(),
        card_with_pedestrian(),
        card_unknown_type(),
    ]
