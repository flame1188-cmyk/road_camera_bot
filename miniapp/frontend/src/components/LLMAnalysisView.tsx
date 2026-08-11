/**
 * LLMAnalysisView — вкладка «ИИ-анализ».
 *
 * Логика:
 *  1. Проверяем доступность провайдеров (free/paid)
 *  2. Пользователь выбирает провайдера (radio)
 *  3. Раздел «Резюме»: кнопка «Сгенерировать» → SSE-стрим → текст по мере поступления
 *  4. Раздел «Вопрос-ответ»: input + «Спросить» → SSE-стрим → ответ по мере поступления
 *  5. История вопросов сохраняется на сервере (последние 10)
 *
 * Sprint 5: polling fallback (?wait=25) полностью удалён.
 *  - Резюме — единственный источник правды: streamingSummary + finalSummary
 *  - При монтировании: one-shot GET /llm/summary (без wait) для cache-hit
 *  - Стрим: SSE, после onDone — finalSummary = streamingSummary
 *  - Q&A: onDone использует streamingQA.answer, не дёргает qa-history
 *  - Markdown-рендер: bold/italic/headings/lists/code
 *
 * Sprint 6: сохранение LLM-сессий в PostgreSQL + UX-правки.
 *  - Backend: после стрима summary/QA — fire-and-forget save в llm_sessions.
 *  - При открытии задачи: get_task_async() восстанавливает llm_summary_state
 *    и llm_qa_history из БД (если они не были восстановлены из in-memory).
 *  - UI: кнопки «⧉ Копировать» (финальный ответ + partial во время стрима
 *    + резюме) и «↻ Повторить» (запускает новый стрим с тем же вопросом).
 *  - CopyButton с fallback на execCommand для не-secure context (Telegram WebView).
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  api,
  type LLMProvidersResponse,
  type LLMSummaryResponse,
  type QAHistoryItem,
  type TaskStatusResponse,
} from '@/lib/api'
import { haptic } from '@/lib/telegram'
import { MarkdownText } from '@/components/MarkdownText'

interface LLMAnalysisViewProps {
  task: TaskStatusResponse
}

const SUGGESTED_QUESTIONS = [
  'В какие дни недели происходит больше всего ДТП?',
  'Какие основные причины роста аварийности?',
  'Где наблюдаются наиболее опасные участки?',
  'Какие рекомендации по снижению ДТП с пешеходами?',
  'Как влияет время суток на тяжесть последствий?',
  'Какова доля нетрезвых водителей в ДТП?',
  // Новые вопросы для Этапов 1-2 (БДД-экспертиза + профиль ТС):
  'Какие недостатки дороги чаще всего способствуют ДТП?',
  'На каких участках УДС (перекрёстки, переходы) больше аварий?',
  'Как состояние покрытия влияет на тяжесть последствий?',
  'В ДТП с каким возрастом ТС больше погибших?',
  'Какие марки автомобилей чаще всего фигурируют в ДТП?',
  'Как распределены ДТП по количеству участвующих ТС?',
]

// Тикер для обновления elapsed-time раз в секунду.
// Используем отдельный state, чтобы не плодить re-render'ы всего компонента.
function useElapsedSeconds(startedAt: string | null | undefined): number {
  const [, setTick] = useState(0)
  useEffect(() => {
    if (!startedAt) return
    const id = setInterval(() => setTick((t) => t + 1), 1000)
    return () => clearInterval(id)
  }, [startedAt])
  if (!startedAt) return 0
  const start = new Date(startedAt).getTime()
  if (isNaN(start)) return 0
  return Math.max(0, Math.floor((Date.now() - start) / 1000))
}

function formatElapsed(sec: number): string {
  if (sec < 60) return `${sec} сек`
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${m} мин ${s} сек`
}

export function LLMAnalysisView({ task }: LLMAnalysisViewProps) {
  const [providers, setProviders] = useState<LLMProvidersResponse | null>(null)
  const [provider, setProvider] = useState<'free' | 'paid'>('free')
  const [started, setStarted] = useState(false)
  // Локальный флаг «нажали кнопку, ждём первый ответ от API».
  // Нужен, чтобы показать прогресс-бар МГНОВЕННО, не дожидаясь
  // первого long-polling ответа (который может идти 25 сек).
  const [starting, setStarting] = useState(false)

  // === Q&A (Sprint 4: streaming) ===
  const [question, setQuestion] = useState('')
  const [qaLoading, setQaLoading] = useState(false)
  const [qaError, setQaError] = useState<string | null>(null)
  const [qaHistory, setQaHistory] = useState<QAHistoryItem[]>([])
  // Streaming-ответ, который ещё не сохранён в history.
  // Показываем его отдельной карточкой с «typing cursor».
  const [streamingQA, setStreamingQA] = useState<{
    question: string
    answer: string
    provider: 'free' | 'paid'
  } | null>(null)
  // AbortController для отмены стрима (кнопка «Стоп»)
  const qaAbortRef = useRef<AbortController | null>(null)

  // Sprint 5: 429-cooldown для Q&A.
  // Если сервер вернул 429 (retryable), показываем обратный отсчёт
  // и блокируем кнопку «Спросить» на это время. Таймер сбрасывается
  // при успешном запросе или ручной смене вопроса.
  const [qaCooldownUntil, setQaCooldownUntil] = useState<number | null>(null)
  // Тикер раз в 1 сек — чтобы обратный отсчёт обновлялся.
  const [, setQaCooldownTick] = useState(0)
  useEffect(() => {
    if (!qaCooldownUntil) return
    const id = setInterval(() => setQaCooldownTick((t) => t + 1), 1000)
    return () => clearInterval(id)
  }, [qaCooldownUntil])
  // Оставшиеся секунды cooldown'а (0 если не активен).
  const qaCooldownSec = qaCooldownUntil
    ? Math.max(0, Math.ceil((qaCooldownUntil - Date.now()) / 1000))
    : 0

  // Sprint 5: счётчик auto-retry на 429 (не более 1 авто-ретрая,
  // чтобы не зацикливаться — итого максимум 2 попытки).
  const qaRetryRef = useRef<number>(0)

  // === Summary (Sprint 4: streaming) ===
  // Streaming-резюме — накапливается по delta.
  const [streamingSummary, setStreamingSummary] = useState<string>('')
  const [summaryStreaming, setSummaryStreaming] = useState(false)
  const summaryAbortRef = useRef<AbortController | null>(null)

  // 3 случайных подсказки из полного списка — при каждом монтировании
  // компонента пользователь видит разные, что расширяет охват возможностей
  // (теперь включает БДД-факторы и профиль ТС).
  const suggestedQuestions = useMemo(
    () => [...SUGGESTED_QUESTIONS].sort(() => Math.random() - 0.5).slice(0, 3),
    [],
  )

  // === Sprint 5: one-shot загрузка готового резюме из кэша ===
  // При первом открытии вкладки — если summary уже готово на сервере,
  // показываем его без запуска стрима (cache hit).
  // polling с ?wait=25 удалён — он был лишним после перехода на SSE.
  const [cachedSummary, setCachedSummary] = useState<{
    text: string
    provider: string
    generated_at: string
  } | null>(null)
  const [summaryError, setSummaryError] = useState<string | null>(null)
  const summaryStartedAtRef = useRef<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api.getLLMSummary(task.task_id, 0).then((resp: LLMSummaryResponse) => {
      if (cancelled) return
      if (resp.state.status === 'done' && resp.result) {
        setCachedSummary(resp.result)
        setStarted(true)
      } else if (resp.state.status === 'failed') {
        setSummaryError(resp.state.error ?? 'Ошибка генерации')
      }
    }).catch(() => {
      // тихо — пользователь может запустить генерацию вручную
    })
    return () => { cancelled = true }
  }, [task.task_id])

  // Elapsed time — пока статус running, показываем сколько секунд идёт анализ
  const isRunning = summaryStreaming || starting
  const elapsedSec = useElapsedSeconds(summaryStartedAtRef.current)
  // Если прошло больше 90 сек — показываем предупреждение, что это дольше обычного
  const isSlow = isRunning && elapsedSec > 90
  // Если прошло больше 240 сек (4 мин) — показываем рекомендацию отменить
  const isVerySlow = isRunning && elapsedSec > 240

  // Загружаем провайдеров и историю
  useEffect(() => {
    api.getLLMProvidersForTask(task.task_id).then(setProviders).catch(() => {})
    api.getQAHistory(task.task_id).then(setQaHistory).catch(() => {})
  }, [task.task_id])

  // Сброс ошибки при переключении провайдера
  useEffect(() => {
    setSummaryError(null)
  }, [provider])

  // Авто-выбор доступного провайдера
  useEffect(() => {
    if (providers) {
      if (provider === 'free' && !providers.free && providers.paid) {
        setProvider('paid')
      }
      if (provider === 'paid' && !providers.paid && providers.free) {
        setProvider('free')
      }
    }
  }, [providers, provider])

  // Cleanup: отменяем стримы при размонтировании
  useEffect(() => {
    return () => {
      qaAbortRef.current?.abort()
      summaryAbortRef.current?.abort()
    }
  }, [])

  // === Summary streaming (Sprint 5: без polling fallback) ===
  const handleGenerateStream = async () => {
    setStarting(true)
    setStarted(true)
    setSummaryStreaming(true)
    setStreamingSummary('')
    setCachedSummary(null)
    setSummaryError(null)
    summaryStartedAtRef.current = new Date().toISOString()
    haptic('medium')

    // Отменяем предыдущий стрим, если был
    summaryAbortRef.current?.abort()
    const controller = new AbortController()
    summaryAbortRef.current = controller

    try {
      await api.getLLMSummaryStream(
        task.task_id,
        provider,
        {
          onDelta: (delta) => {
            setStreamingSummary((prev) => prev + delta)
            setStarting(false)  // первый токен пришёл — больше не "запускаем"
          },
          onDone: () => {
            setSummaryStreaming(false)
            setStarting(false)
            // Sprint 5: берём финальный текст из streamingSummary,
            // НЕ дёргаем /llm/summary?wait=25 — он лишний.
            setStreamingSummary((finalText) => {
              if (finalText.trim()) {
                setCachedSummary({
                  text: finalText,
                  provider,
                  generated_at: new Date().toISOString(),
                })
              }
              return finalText
            })
            summaryStartedAtRef.current = null
            haptic('success')
          },
          onError: (err) => {
            setSummaryStreaming(false)
            setStarting(false)
            setSummaryError(err)
            summaryStartedAtRef.current = null
            haptic('error')
          },
        },
        controller.signal,
      )
    } catch (e: any) {
      if (e?.name !== 'AbortError') {
        setSummaryStreaming(false)
        setStarting(false)
        setSummaryError(e?.message ?? 'Ошибка запроса')
        summaryStartedAtRef.current = null
      }
    }
  }

  const handleStopSummary = () => {
    summaryAbortRef.current?.abort()
    setSummaryStreaming(false)
    setStarting(false)
    summaryStartedAtRef.current = null
    haptic('light')
    // Частичный текст сохраняем как кэшированный (с маркером)
    setStreamingSummary((partial) => {
      if (partial.trim().length > 30) {
        setCachedSummary({
          text: partial + '\n\n_[генерация прервана пользователем]_',
          provider,
          generated_at: new Date().toISOString(),
        })
      }
      return partial
    })
  }

  // === Q&A streaming (Sprint 4 + Sprint 5: 429 auto-retry) ===
  const handleAskStream = async (overrideQuestion?: string) => {
    const trimmed = (overrideQuestion ?? question).trim()
    if (!trimmed) return

    // Sprint 5: если cooldown активен — не даём отправить.
    if (qaCooldownSec > 0) {
      setQaError(
        `Подождите ещё ${qaCooldownSec} сек перед новым запросом ` +
        `(защита от перегрузки сервиса нейросети).`
      )
      haptic('error')
      return
    }

    setQaError(null)
    setQaLoading(true)
    setStreamingQA({
      question: trimmed,
      answer: '',
      provider,
    })
    haptic('medium')

    // Отменяем предыдущий стрим, если был
    qaAbortRef.current?.abort()
    const controller = new AbortController()
    qaAbortRef.current = controller

    try {
      await api.askLLMStream(
        task.task_id,
        trimmed,
        provider,
        {
          onDelta: (delta) => {
            setStreamingQA((prev) => prev
              ? { ...prev, answer: prev.answer + delta }
              : prev,
            )
          },
          onDone: () => {
            // Sprint 5: успешное завершение — сбрасываем retry-счётчик и cooldown.
            qaRetryRef.current = 0
            setQaCooldownUntil(null)

            // Sprint 5: streamingQA.answer — единственный источник правды.
            // Старый fallback с api.getQAHistory удалён — стриминг работает,
            // и запрос истории был лишним (давал дубликат).
            setStreamingQA((final) => {
              if (final && final.answer.trim()) {
                setQaHistory((prev) => [
                  {
                    question: final.question,
                    answer: final.answer,
                    provider: final.provider,
                    timestamp: new Date().toISOString(),
                  },
                  ...prev,
                ])
              } else {
                // Защита от пустого ответа — без запроса qa-history
                setQaError('Ответ пустой. Попробуйте переформулировать вопрос или сменить провайдер.')
              }
              return null
            })
            setQaLoading(false)
            setQuestion('')
            haptic('success')
          },
          onError: (err, retryable, errorType) => {
            // Sprint 5: если сервер вернул retryable-ошибку (429/5xx)
            // и мы ещё не делали auto-retry — пробуем снова через 30 сек.
            // Backend уже сделал свои 2 retry (с 30+45 сек backoff),
            // значит ZhipuAI всё ещё троттлит. Ждём 30 сек на клиенте
            // и делаем 1 финальную попытку.
            if (
              retryable &&
              errorType === 'HTTPStatusError' &&
              qaRetryRef.current < 1 &&
              !controller.signal.aborted
            ) {
              qaRetryRef.current += 1
              const retryInSec = 30
              setQaCooldownUntil(Date.now() + retryInSec * 1000)
              setQaError(
                `${err}\n\n⏳ Авто-повтор через ${retryInSec} сек...`
              )
              // Не сбрасываем qaLoading — оставляем спиннер.
              // Через 30 сек делаем повторный запрос с тем же вопросом.
              setTimeout(() => {
                if (controller.signal.aborted) return
                void handleAskStream(trimmed)
              }, retryInSec * 1000)
              return
            }

            // Sprint 5.1: EmptyResponseError — GLM-4.7-Flash «выдохся» на
            // reasoning (chunks=0, completion=max_tokens). Backend НЕ делает
            // retry (это не 429, а success с пустым content). Делаем 1
            // auto-retry на клиенте с короткой задержкой 5 сек — обычно
            // следующий запрос проходит успешно.
            if (
              retryable &&
              errorType === 'EmptyResponseError' &&
              qaRetryRef.current < 1 &&
              !controller.signal.aborted
            ) {
              qaRetryRef.current += 1
              const retryInSec = 5
              setQaCooldownUntil(Date.now() + retryInSec * 1000)
              setQaError(
                `${err}\n\n⏳ Авто-повтор через ${retryInSec} сек...`
              )
              setTimeout(() => {
                if (controller.signal.aborted) return
                void handleAskStream(trimmed)
              }, retryInSec * 1000)
              return
            }

            // Не retryable или retries закончились — показываем ошибку.
            setStreamingQA(null)
            setQaError(err)
            setQaLoading(false)
            qaRetryRef.current = 0
            // После финальной ошибки — короткий cooldown 10 сек,
            // чтобы пользователь не нажал «Спросить» сразу же.
            if (retryable) {
              setQaCooldownUntil(Date.now() + 10 * 1000)
            }
            haptic('error')
          },
        },
        controller.signal,
      )
    } catch (e: any) {
      if (e?.name !== 'AbortError') {
        setStreamingQA(null)
        setQaError(e?.message ?? 'Ошибка запроса')
        setQaLoading(false)
        qaRetryRef.current = 0
        haptic('error')
      } else {
        // Abort — сохраняем partial в историю, если есть
        setStreamingQA((partial) => {
          if (partial && partial.answer.trim().length > 10) {
            setQaHistory((prev) => [
              {
                question: partial.question,
                answer: partial.answer + '\n\n_[ответ прерван пользователем]_',
                provider: partial.provider,
                timestamp: new Date().toISOString(),
              },
              ...prev,
            ])
          }
          return null
        })
        setQaLoading(false)
        qaRetryRef.current = 0
      }
    }
  }

  const handleStopQA = () => {
    qaAbortRef.current?.abort()
    haptic('light')
    // onDone/onError не вызвался — обрабатываем здесь
    setStreamingQA((partial) => {
      if (partial && partial.answer.trim().length > 10) {
        setQaHistory((prev) => [
          {
            question: partial.question,
            answer: partial.answer + '\n\n_[ответ прерван пользователем]_',
            provider: partial.provider,
            timestamp: new Date().toISOString(),
          },
          ...prev,
        ])
      }
      return null
    })
    setQaLoading(false)
  }

  // === Заглушка если LLM не настроен ===
  if (providers && !providers.free && !providers.paid) {
    return (
      <div className="tg-card text-center py-6">
        <div className="text-3xl mb-2">🤖</div>
        <div className="font-medium mb-1">ИИ-анализ недоступен</div>
        <div className="text-xs opacity-70">
          Не настроен ни один LLM-провайдер.
          <br />
          Задайте <code>LLM_API_KEY</code> в переменных окружения для
          бесплатного анализа через GLM (ZhipuAI).
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {/* Выбор провайдера */}
      {providers && (providers.free || providers.paid) && (
        <div className="tg-card">
          <div className="tg-section-header mb-2">Провайдер ИИ</div>
          <div className="grid grid-cols-2 gap-1.5">
            <ProviderButton
              active={provider === 'free'}
              disabled={!providers.free}
              onClick={() => {
                setProvider('free')
                haptic('light')
              }}
              title="Бесплатный"
              subtitle={providers.free_model || 'GLM'}
              icon="⚡"
            />
            <ProviderButton
              active={provider === 'paid'}
              disabled={!providers.paid}
              onClick={() => {
                setProvider('paid')
                haptic('light')
              }}
              title="Полный"
              subtitle={providers.paid_model || 'DeepSeek'}
              icon="🔬"
            />
          </div>
          <div className="text-[10px] opacity-60 mt-2">
            ⚡ Быстрый (15-30с) — агрегированные метрики + кросс-таблицы.
            <br />
            🔬 Полный (30-90с) — все данные участников ДТП.
          </div>
        </div>
      )}

      {/* Раздел: Резюме */}
      <div className="tg-card">
        <div className="tg-section-header mb-2">Аналитическое резюме</div>

        {!started && !cachedSummary && !starting && !summaryStreaming && !summaryError && (
          <>
            <p className="text-sm opacity-80 mb-3">
              Нейросеть проанализирует метрики ДТП, кросс-таблицы
              корреляций и очаги (если рассчитаны), затем сформирует
              развёрнутое резюме с рекомендациями. Текст появится здесь
              по мере генерации (token-by-token) с markdown-форматированием.
            </p>
            <button
              onClick={handleGenerateStream}
              disabled={!providers}
              className="w-full py-2.5 rounded-xl font-medium text-sm disabled:opacity-50"
              style={{
                backgroundColor: 'var(--tg-color-button, #2481cc)',
                color: 'var(--tg-color-button-text, #ffffff)',
              }}
            >
              🤖 Сгенерировать резюме
            </button>
            <p className="text-xs opacity-60 mt-2 text-center">
              {provider === 'free' ? '15-30 секунд' : '30-90 секунд'}
            </p>
          </>
        )}

        {(summaryStreaming || starting) && (
          <div className="text-center py-4">
            <div className="text-3xl mb-2 animate-pulse">{isVerySlow ? '⏰' : '⏳'}</div>
            <div className="font-medium mb-1">
              {isVerySlow
                ? 'Анализ идёт дольше обычного...'
                : starting && !streamingSummary
                  ? 'Запуск нейросети...'
                  : 'Нейросеть генерирует...'}
            </div>
            <div className="text-xs opacity-70 mb-3">
              {starting && !streamingSummary
                ? 'Подготовка промпта...'
                : 'Стриминг ответа...'}
            </div>
            {/* Elapsed time */}
            {elapsedSec >= 5 && (
              <div
                className="text-xs mb-3 font-mono"
                style={{
                  color: isVerySlow
                    ? '#ff3b30'
                    : isSlow
                      ? '#ff9500'
                      : 'var(--tg-color-subtitle, #888)',
                }}
              >
                ⏱ {formatElapsed(elapsedSec)}
                {isSlow && !isVerySlow && ' — дольше обычного'}
                {isVerySlow && ' — вероятно, сбой нейросети'}
              </div>
            )}
            {/* Подсказка при долгом ожидании */}
            {isVerySlow && (
              <div
                className="text-xs p-2 rounded-lg mb-3 text-left"
                style={{
                  backgroundColor: 'rgba(255, 149, 0, 0.1)',
                  color: '#ff9500',
                }}
              >
                Сервис нейросети не отвечает достаточно долго. Подождите ещё
                минуту или нажмите «Стоп» и попробуйте другой провайдер.
              </div>
            )}
            {/* Streaming text — показываем по мере поступления (markdown) */}
            {streamingSummary && (
              <div
                className="text-sm leading-relaxed text-left mb-3"
                style={{
                  fontFamily:
                    '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
                  borderLeft: '2px solid var(--tg-color-button, #2481cc)',
                  paddingLeft: '8px',
                }}
              >
                <MarkdownText text={streamingSummary} streaming />
              </div>
            )}
            {/* Прогресс-бар (фаза подготовки промпта, до первого токена) */}
            {!streamingSummary && (
              <>
                <div
                  className="w-full h-2 rounded-full overflow-hidden"
                  style={{
                    backgroundColor: 'var(--tg-color-secondary-bg, #f1f1f1)',
                  }}
                >
                  <div
                    className="h-full transition-all duration-500"
                    style={{
                      width: '5%',
                      backgroundColor: isVerySlow
                        ? '#ff3b30'
                        : isSlow
                          ? '#ff9500'
                          : 'var(--tg-color-button, #2481cc)',
                    }}
                  />
                </div>
                <div className="text-xs opacity-60 mt-1">5%</div>
              </>
            )}
            {/* Кнопка «Стоп» — во время стрима всегда доступна */}
            <button
              onClick={handleStopSummary}
              className="mt-3 text-xs px-3 py-1.5 rounded-lg"
              style={{
                backgroundColor: 'var(--tg-color-secondary-bg, #f1f1f1)',
                color: 'var(--tg-color-text, #000)',
              }}
            >
              ✕ Остановить
            </button>
          </div>
        )}

        {/* Ошибка генерации */}
        {summaryError && !summaryStreaming && (
          <div className="text-center py-4">
            <div className="text-3xl mb-2">❌</div>
            <div className="font-medium mb-1" style={{ color: '#ff3b30' }}>
              Ошибка генерации
            </div>
            <div className="text-xs opacity-80 mb-3">
              {summaryError}
            </div>
            <button
              onClick={handleGenerateStream}
              className="px-4 py-2 rounded-xl text-sm font-medium"
              style={{
                backgroundColor: 'var(--tg-color-button, #2481cc)',
                color: 'var(--tg-color-button-text, #ffffff)',
              }}
            >
              Повторить
            </button>
          </div>
        )}

        {/* Готовое резюме (из кэша или после стрима) */}
        {cachedSummary && !summaryStreaming && !summaryError && (
          <>
            <div className="flex items-center justify-between mb-2 gap-2">
              <div className="text-xs opacity-60">
                Провайдер: {cachedSummary.provider === 'free' ? '⚡' : '🔬'}{' '}
                {cachedSummary.provider}
              </div>
              <div className="flex items-center gap-1">
                {/* Sprint 6: копировать резюме — часто просят передать
                    аналитику в Telegram-чат или сохранить локально. */}
                <CopyButton text={cachedSummary.text} />
                <button
                  onClick={handleGenerateStream}
                  className="text-xs px-2 py-1 rounded-lg"
                  style={{
                    backgroundColor:
                      'var(--tg-color-secondary-bg, #f1f1f1)',
                    color: 'var(--tg-color-text, #000)',
                  }}
                >
                  ↻ Перегенерировать
                </button>
              </div>
            </div>
            <div
              className="text-sm leading-relaxed"
              style={{
                fontFamily:
                  '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
              }}
            >
              <MarkdownText text={cachedSummary.text} />
            </div>
          </>
        )}
      </div>

      {/* Раздел: Вопрос-ответ */}
      <div className="tg-card">
        <div className="tg-section-header mb-2">Спросить нейросеть</div>

        <div className="space-y-2 mb-3">
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="Например: в какие часы происходит больше всего ДТП?"
            rows={2}
            className="w-full px-3 py-2 rounded-lg text-sm resize-none"
            style={{
              backgroundColor: 'var(--tg-color-secondary-bg, #f1f1f1)',
              color: 'var(--tg-color-text, #000)',
              border: 'none',
              outline: 'none',
            }}
          />

        {/* Подсказки */}
          {!question && !qaLoading && (
            <div className="flex flex-wrap gap-1.5">
              {suggestedQuestions.map((q) => (
                <button
                  key={q}
                  onClick={() => {
                    setQuestion(q)
                    haptic('light')
                  }}
                  className="text-xs px-2 py-1 rounded-full"
                  style={{
                    backgroundColor:
                      'var(--tg-color-secondary-bg, #f1f1f1)',
                    color: 'var(--tg-color-text, #000)',
                    opacity: 0.8,
                  }}
                >
                  {q}
                </button>
              ))}
            </div>
          )}

          {!qaLoading ? (
            <button
              onClick={() => void handleAskStream()}
              disabled={!question.trim() || qaCooldownSec > 0}
              className="w-full py-2 rounded-xl text-sm font-medium disabled:opacity-50"
              style={{
                backgroundColor: qaCooldownSec > 0
                  ? 'var(--tg-color-secondary-bg, #999)'
                  : 'var(--tg-color-button, #2481cc)',
                color: qaCooldownSec > 0
                  ? 'var(--tg-color-text, #666)'
                  : 'var(--tg-color-button-text, #ffffff)',
              }}
            >
              {qaCooldownSec > 0
                ? `⏳ Подождите ${qaCooldownSec} сек…`
                : '💬 Спросить'}
            </button>
          ) : (
            <button
              onClick={handleStopQA}
              className="w-full py-2 rounded-xl text-sm font-medium"
              style={{
                backgroundColor: 'rgba(255, 59, 48, 0.15)',
                color: '#ff3b30',
              }}
            >
              ⏹ Остановить генерацию
            </button>
          )}

          {qaError && (
            <div
              className="text-xs p-2 rounded-lg"
              style={{
                backgroundColor: 'rgba(255, 59, 48, 0.1)',
                color: '#ff3b30',
              }}
            >
              {qaError}
            </div>
          )}
        </div>

        {/* Streaming-ответ (показываем во время генерации) */}
        {streamingQA && (
          <div className="space-y-2 pt-2 border-t border-current/10">
            <div className="text-xs opacity-60 mb-1">Генерация ответа...</div>
            <StreamingQACard
              question={streamingQA.question}
              answer={streamingQA.answer}
              provider={streamingQA.provider}
            />
          </div>
        )}

        {/* История вопросов */}
        {!streamingQA && qaHistory.length > 0 && (
          <div className="space-y-2 pt-2 border-t border-current/10">
            <div className="text-xs opacity-60 mb-1">История:</div>
            {qaHistory.map((item, idx) => (
              <QACard
                key={idx}
                item={item}
                onRepeat={(q) => {
                  // Sprint 6: кнопка «Повторить» — копирует вопрос в input
                  // и сразу запускает новый стрим. Если в данный момент
                  // идёт другой стрим — он отменяется через handleAskStream
                  // (controller.abort() в первой строке).
                  setQuestion(q)
                  void handleAskStream(q)
                }}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ============================================================
// Подкомпоненты
// ============================================================
function ProviderButton({
  active,
  disabled,
  onClick,
  title,
  subtitle,
  icon,
}: {
  active: boolean
  disabled: boolean
  onClick: () => void
  title: string
  subtitle: string
  icon: string
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="p-2.5 rounded-xl text-left transition-all disabled:opacity-40"
      style={{
        backgroundColor: active
          ? 'var(--tg-color-button, #2481cc)'
          : 'var(--tg-color-secondary-bg, #f1f1f1)',
        color: active
          ? 'var(--tg-color-button-text, #ffffff)'
          : 'var(--tg-color-text, #000)',
      }}
    >
      <div className="flex items-center gap-1.5">
        <span>{icon}</span>
        <span className="text-sm font-medium">{title}</span>
      </div>
      <div
        className="text-[10px] mt-0.5"
        style={{ opacity: active ? 0.9 : 0.6 }}
      >
        {subtitle}
      </div>
    </button>
  )
}

// Streaming-карточка: показывает partial-ответ с typing-cursor.
function StreamingQACard({
  question,
  answer,
  provider,
}: {
  question: string
  answer: string
  provider: 'free' | 'paid'
}) {
  return (
    <div
      className="rounded-lg p-2.5"
      style={{
        backgroundColor: 'var(--tg-color-secondary-bg, #f1f1f1)',
        borderLeft: '2px solid var(--tg-color-button, #2481cc)',
      }}
    >
      <div className="text-xs font-medium mb-1 opacity-80">
        ❓ {question}
      </div>
      <div className="text-xs leading-relaxed">
        <MarkdownText text={answer} streaming />
      </div>
      <div className="flex items-center justify-between mt-1 gap-2">
        <div className="text-[10px] opacity-50">
          {provider === 'free' ? '⚡ GLM' : '🔬 DeepSeek'} · генерация...
        </div>
        {/* Sprint 6: копировать partial-ответ — полезно, если пользователь
            устал ждать и хочет сохранить уже сгенерированный кусок. */}
        {answer.trim().length > 20 && (
          <CopyButton text={answer} />
        )}
      </div>
    </div>
  )
}

// Sprint 6: унифицированная кнопка «копировать» — используется и в
// StreamingQACard (partial), и в QACard (финальный ответ). Имеет
// встроенный fallback для не-secure context (Telegram WebView на
// HTTP-доменах), где navigator.clipboard может быть недоступен.
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text)
      } else {
        // Fallback: временный textarea + execCommand('copy').
        // Устаревший API, но единственный надёжный способ в старых WebView.
        const textarea = document.createElement('textarea')
        textarea.value = text
        textarea.style.position = 'fixed'
        textarea.style.opacity = '0'
        document.body.appendChild(textarea)
        textarea.select()
        document.execCommand('copy')
        document.body.removeChild(textarea)
      }
      setCopied(true)
      haptic('success')
      setTimeout(() => setCopied(false), 2000)
    } catch (e) {
      console.warn('Copy failed:', e)
      haptic('error')
    }
  }

  return (
    <button
      onClick={handleCopy}
      className="text-[10px] px-2 py-0.5 rounded-md transition-all"
      style={{
        backgroundColor: copied
          ? 'rgba(48, 209, 88, 0.15)'
          : 'var(--tg-color-secondary-bg, #e5e5e5)',
        color: copied
          ? '#30d158'
          : 'var(--tg-color-text, #000)',
        border: copied
          ? '1px solid rgba(48, 209, 88, 0.4)'
          : '1px solid transparent',
      }}
      title="Скопировать ответ в буфер обмена"
    >
      {copied ? '✓ Скопировано' : '⧉ Копировать'}
    </button>
  )
}

function QACard({
  item,
  onRepeat,
}: {
  item: QAHistoryItem
  onRepeat: (question: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const answerPreview = item.answer.slice(0, 200)
  const hasMore = item.answer.length > 200

  return (
    <div
      className="rounded-lg p-2.5"
      style={{
        backgroundColor: 'var(--tg-color-secondary-bg, #f1f1f1)',
      }}
    >
      <div className="text-xs font-medium mb-1 opacity-80">
        ❓ {item.question}
      </div>
      <div className="text-xs leading-relaxed">
        <MarkdownText text={expanded || !hasMore ? item.answer : answerPreview + '...'} />
      </div>
      {hasMore && (
        <button
          onClick={() => {
            setExpanded(!expanded)
            haptic('light')
          }}
          className="text-xs mt-1 opacity-70"
        >
          {expanded ? 'Свернуть' : 'Читать далее'}
        </button>
      )}
      <div className="flex items-center justify-between mt-2 gap-2">
        <div className="text-[10px] opacity-50">
          {item.provider === 'free' ? '⚡ GLM' : '🔬 DeepSeek'} ·{' '}
          {new Date(item.timestamp).toLocaleString('ru-RU', {
            day: 'numeric',
            month: 'short',
            hour: '2-digit',
            minute: '2-digit',
          })}
        </div>
        {/* Sprint 6: кнопки «копировать» и «повторить» — компактные,
            в правой части карточки. Иконки + текст, чтобы было понятно
            даже без подсказок. */}
        <div className="flex items-center gap-1">
          <CopyButton text={item.answer} />
          <button
            onClick={() => {
              haptic('light')
              onRepeat(item.question)
            }}
            className="text-[10px] px-2 py-0.5 rounded-md transition-all"
            style={{
              backgroundColor: 'var(--tg-color-secondary-bg, #e5e5e5)',
              color: 'var(--tg-color-text, #000)',
              border: '1px solid transparent',
            }}
            title="Задать этот вопрос ещё раз (переформулировать или получить новый ответ)"
          >
            ↻ Повторить
          </button>
        </div>
      </div>
    </div>
  )
}
