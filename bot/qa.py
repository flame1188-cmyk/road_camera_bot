"""bot.qa — Q&A-режим с LLM (вопросы по данным).

Содержит:
  • _handle_analytics_question — обработка текстового вопроса
    пользователя по текущим данным ДТП

Выделено из единого bot.py (Phase 3-2). 100% pure.
"""
from bot._state import *
from bot.infra import _tg_retry, _send_long_message, _sanitize_error
from bot.analysis import _get_current_cards

async def _handle_analytics_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    question: str,
    comparison: dict,
    reg_name: str,
    current_label: str,
    prev_label: str,
    llm_provider: str = "free",
) -> None:
    """
    Обрабатывает вопрос пользователя по данным аналитики.
    Отправляет вопрос в LLM и возвращает ответ.
    """
    chat_id = update.effective_chat.id

    # Индикатор набора
    wait_msg = await update.message.reply_text(
        "\U0001F916 Анализирую вопрос...\n"
        "⏳ Обычно занимает 15-30 секунд."
    )

    try:
        # Формируем дополнение из сырых карточек (если есть)
        # Для вопросов берём меньше карточек — только статистику + 15 самых тяжёлых
        raw_sup = ""
        current_cards = _get_current_cards(context) or []
        prev_cards = context.user_data.get("analytics_prev_cards", [])
        if current_cards or prev_cards:
            raw_sup = extract_raw_supplement(current_cards, current_label, max_cards=15)
            raw_sup += extract_raw_supplement(prev_cards, prev_label, max_cards=15)

        # Кросс-таблицы для бесплатного метода (GLM)
        cross_tables_ctx = ""
        if llm_provider == "free" and current_cards:
            try:
                from analytics import (
                    calculate_cross_tables, calculate_statistical_metrics,
                )
                current_cross = calculate_cross_tables(current_cards)
                prev_cross = None
                if prev_cards:
                    prev_cross = calculate_cross_tables(prev_cards)
                from llm_analyzer import (
                    format_cross_tables_for_prompt,
                    format_statistical_metrics_for_prompt,
                )
                cross_tables_ctx = format_cross_tables_for_prompt(
                    current_cross, prev_cross, current_label, prev_label,
                )
                # Этап 2: статистические метрики (severity rates, Z-score, χ²)
                stats = calculate_statistical_metrics(current_cross)
                stats_text = format_statistical_metrics_for_prompt(stats)
                if stats_text and not stats_text.endswith("(недостаточно данных для статистического анализа)"):
                    cross_tables_ctx += "\n\n" + stats_text
            except Exception as e:
                logger.warning(f"Не удалось построить кросс-таблицы для Q&A: {e}")

        answer = await get_ai_answer(
            question=question,
            comparison=comparison,
            reg_name=reg_name,
            current_label=current_label,
            prev_label=prev_label,
            raw_supplement=raw_sup,
            news_context=context.user_data.get("analytics_news_context", ""),
            clusters_context=format_clusters_for_prompt(
                context.user_data.get("analytics_clusters", [])
            ),
            cross_tables_context=cross_tables_ctx,
            provider=llm_provider,
            # Передаём историю диалога, чтобы LLM понимала follow-up-вопросы
            history=context.user_data.get("qa_history", []),
        )

        # Сохраняем пару (вопрос, ответ) в историю диалога.
        # История хранится в OpenAI-формате: [{role, content}, ...]
        # и обрезается до последних _QA_HISTORY_MAX_MESSAGES сообщений
        # (6 пар Q&A), чтобы не раздувать промпт сверх лимита контекста.
        qa_history = context.user_data.get("qa_history", [])
        qa_history.append({"role": "user", "content": question})
        qa_history.append({"role": "assistant", "content": answer})
        if len(qa_history) > _QA_HISTORY_MAX_MESSAGES:
            qa_history = qa_history[-_QA_HISTORY_MAX_MESSAGES:]
        context.user_data["qa_history"] = qa_history

        # Удаляем индикатор
        try:
            await wait_msg.delete()
        except Exception:
            pass

        # Клавиатура с кнопками «В меню» / «Завершить»
        qa_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "\u21A9\uFE0F В меню",
                callback_data="back_to_menu",
            )],
            [InlineKeyboardButton(
                "\u274C Завершить",
                callback_data="end_qa",
            )],
        ])

        # Отправляем ответ (экранируем и вопрос, и ответ LLM)
        # Fallback: если HTML-парсинг ломается — отправляем без форматирования
        try:
            await _send_long_message(
                context.bot, chat_id,
                text=(
                    f"\U0001F916 <b>Вопрос:</b> {html_mod.escape(question)}\n\n"
                    f"{html_mod.escape(answer)}"
                ),
                parse_mode="HTML",
                reply_markup=qa_keyboard,
            )
        except Exception:
            # HTML-парсер Telegram не смог обработать — отправляем plain text
            await _send_long_message(
                context.bot, chat_id,
                text=f"\U0001F916 Вопрос: {question}\n\n{answer}",
                parse_mode=None,
                reply_markup=qa_keyboard,
            )

    except Exception as e:
        logger.error(f"Ошибка при ответе на вопрос: {e}")
        try:
            await wait_msg.edit_text(
                f"\u26A0\uFE0F Не удалось получить ответ от нейросети.\n\n"
                f"Ошибка: {_sanitize_error(e)}\n\n"
                f"Попробуйте переформулировать вопрос или нажмите кнопку ниже."
            )
        except Exception:
            pass




