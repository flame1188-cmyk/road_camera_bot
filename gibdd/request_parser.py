"""
Парсер естественных запросов пользователя.

Парсит запросы вида:
  "Вологодская область за 2025 год"
  "Алтайский край за 3 месяца 2026"
  "март 2025 Вологодская"
  "за I квартал 2025 Москва"
  "2.2024 1101"
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_regions_cache: list[dict[str, str]] | None = None


@dataclass
class ParsedPeriod:
    """Распарсенный период."""
    months: list[int]
    year: int
    label: str

    def get_dat_list(self) -> list[str]:
        """Возвращает список дат в формате 'м.гггг' для API ГИБДД."""
        return [f"{m}.{self.year}" for m in self.months]


async def ensure_regions_loaded() -> list[dict[str, str]]:
    """Загружает справочник регионов (с кэшированием)."""
    global _regions_cache
    if _regions_cache is not None:
        return _regions_cache
    from gibdd.api_client import fetch_regions
    _regions_cache = await fetch_regions()
    return _regions_cache


def find_region(text: str, regions: list[dict[str, str]]) -> dict[str, str] | None:
    """Поиск региона по тексту (нечёткое совпадение)."""
    text_lower = text.lower().strip()

    for r in regions:
        name = r["name"].lower()
        # Точное совпадение
        if name == text_lower:
            return r
        # Частичное совпадение (>50% длины)
        if text_lower in name or name in text_lower:
            return r

    # Часть совпадения по словам
    text_words = set(re.findall(r'\w+', text_lower))
    best_match = None
    best_score = 0
    for r in regions:
        name = r["name"].lower()
        name_words = set(re.findall(r'\w+', name))
        common = text_words & name_words
        score = len(common)
        if score > best_score:
            best_score = score
            best_match = r

    if best_match and best_score >= 2:
        return best_match
    return None


MONTH_NAMES = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4,
    "май": 5, "июнь": 6, "июль": 7, "август": 8,
    "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
    "янв": 1, "фев": 2, "мар": 3, "апр": 4,
    "июн": 6, "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}

MONTH_NAMES_GENITIVE = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


def parse_period(text: str, year: int | None = None) -> ParsedPeriod | None:
    """
    Парсит период из текста.

    Поддерживает:
      "за 3 месяца", "3 месяца", "полугодие"
      "I квартал", "II квартал", "1 квартал"
      "март", "январь-март"
      "за 2025 год", "2025"
    """
    if year is None:
        return None

    text_lower = text.lower().strip()

    # "Весь год" / "за год" / просто год
    if re.search(r'весь\s+год|за\s+год|за\s+\d+\s+год', text_lower) or re.search(r'^\d{4}$', text.strip()):
        return ParsedPeriod(months=list(range(1, 13)), year=year, label=f"Весь {year} год")

    # Кварталы
    quarter_map = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "1": 1, "2": 2, "3": 3, "4": 4}
    q_match = re.search(r'([ivxIVX1-4])\s*квартал', text_lower)
    if q_match:
        q_num = quarter_map.get(q_match.group(1).lower())
        if q_num:
            start = (q_num - 1) * 3 + 1
            end = start + 2
            months = list(range(start, end + 1))
            label = f"{['I','II','III','IV'][q_num-1]} квартал {year}"
            return ParsedPeriod(months=months, year=year, label=label)

    # Полугодие
    half_match = re.search(r'полугодие\s*([12])', text_lower)
    if half_match:
        half = int(half_match.group(1))
        if half == 1:
            return ParsedPeriod(months=list(range(1, 7)), year=year, label=f"Полугодие 1 {year}")
        else:
            return ParsedPeriod(months=list(range(7, 13)), year=year, label=f"Полугодие 2 {year}")

    # "N месяцев"
    n_match = re.search(r'(\d+)\s*месяц', text_lower)
    if n_match:
        n = int(n_match.group(1))
        months = list(range(1, n + 1))
        label = f"{n} мес. {year}"
        return ParsedPeriod(months=months, year=year, label=label)

    # Конкретный месяц
    all_months = {**MONTH_NAMES, **MONTH_NAMES_GENITIVE}
    for name, num in all_months.items():
        if name in text_lower:
            return ParsedPeriod(months=[num], year=year, label=f"{MONTH_NAMES.get(name, name).capitalize()} {year}")

    return None


def parse_user_message(text: str) -> tuple[ParsedPeriod | None, str | None]:
    """
    Парсит текстовое сообщение пользователя.

    Returns:
        (period, region_code) — период и код региона.
        Любое из значений может быть None.
    """
    text = text.strip()

    # Строгий формат: "2.2024 1101"
    strict_match = re.match(r'^(\d{1,2})\.(\d{4})\s+(\d{4})$', text)
    if strict_match:
        month = int(strict_match.group(1))
        year = int(strict_match.group(2))
        reg_code = strict_match.group(3)
        label = f"{month}.{year}"
        return ParsedPeriod(months=[month], year=year, label=label), reg_code

    # Извлекаем год
    year = None
    year_match = re.search(r'\b(19\d{2}|20\d{2})\b', text)
    if year_match:
        year = int(year_match.group(1))

    if not year:
        return None, None

    # Период
    period = parse_period(text, year)

    # Код региона — ищем 4-значное число
    reg_code = None
    code_match = re.search(r'\b(\d{4})\b', text)
    if code_match:
        candidate = code_match.group(1)
        if candidate != str(year):
            reg_code = candidate

    return period, reg_code
