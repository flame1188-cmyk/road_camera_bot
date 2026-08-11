/**
 * ClustersView — вкладка «Очаги ДТП».
 *
 * Логика:
 *  1. На первой загрузке показываем кнопку «Рассчитать очаги»
 *  2. После клика запускается POST /clusters, начинаем polling
 *  3. Показываем прогресс: «Загрузка границ НП...» и т.д.
 *  4. По завершении: KPI-сводка, динамика, карта (iframe), топ-очаги
 *
 * Особенности:
 *  - Расчёт выполняется один раз, результат кэшируется на задаче
 *  - Повторное открытие вкладки мгновенно показывает готовый результат
 *  - Карта очагов — отдельный iframe (Leaflet с маркерами)
 */
import { useEffect, useState } from 'react'
import {
  api,
  type ClusterItem,
  type TaskStatusResponse,
} from '@/lib/api'
import { haptic, isTelegramDesktop } from '@/lib/telegram'
import { useClustersPolling } from '@/hooks/useAnalysisPolling'

interface ClustersViewProps {
  task: TaskStatusResponse
}

const ZONE_LABELS: Record<string, string> = {
  settlement_intersection: 'Перекрёсток в НП',
  settlement_road: 'Участок дороги в НП',
  settlement_segment: 'Участок в НП',
  nonsettlement: 'Вне НП',
}

const DYNAMICS_LABELS: Record<string, { label: string; color: string; icon: string }> = {
  // Новая методология (пикетаж + сосед)
  repeated_growing: { label: 'Повторный (рост)', color: '#ff3b30', icon: '🔄↑' },
  repeated_shrinking: { label: 'Повторный (снижение)', color: '#2481cc', icon: '🔄↓' },
  repeated_stable: { label: 'Повторный (стабильно)', color: '#8e8e93', icon: '🔄→' },
  repeated_merged: { label: 'Повторный (слияние)', color: '#af52de', icon: '🔄⊕' },
  new: { label: 'Новый', color: '#34c759', icon: '🆕' },
  new_with_neighbor: { label: 'Новый (есть ближайший в АППГ)', color: '#ff9500', icon: '🆕↔' },
  // АППГ-очаг, повторённый в текущем (отдельная строка со ссылкой на текущий №)
  prev_matched: { label: 'АППГ (повторён)', color: '#5ac8fa', icon: '🔄' },
  lost: { label: 'Исчезнувший', color: '#9e9e9e', icon: '✗' },
  // Обратная совместимость (старые ключи из сохранённых задач)
  growing: { label: 'Растущие', color: '#ff3b30', icon: '↑' },
  shrinking: { label: 'Снижение', color: '#2481cc', icon: '↓' },
  stable: { label: 'Стабильные', color: '#8e8e93', icon: '→' },
}

export function ClustersView({ task }: ClustersViewProps) {
  const [started, setStarted] = useState(false)
  // Локальный флаг «нажали кнопку, ждём первый ответ от API».
  // Нужен, чтобы показать прогресс-бар МГНОВЕННО, не дожидаясь
  // первого long-polling ответа (который может идти 25 сек).
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)
  const [excelLoading, setExcelLoading] = useState(false)
  const [excelError, setExcelError] = useState<string | null>(null)

  // Опрашиваем только если пользователь запустил расчёт ИЛИ
  // если операция уже была запущена (например, в предыдущей сессии вкладки)
  const {
    data,
    isError,
  } = useClustersPolling(task.task_id, started)

  // Если при загрузке уже есть результат — автоматически показываем его
  useEffect(() => {
    if (data?.state.status === 'done') {
      setStarted(true)
      setStarting(false)
    }
    // Когда пришёл первый ответ со статусом running — локальный loading можно снять
    if (data?.state.status === 'running') {
      setStarting(false)
    }
  }, [data?.state.status])

  const handleStart = async () => {
    setStartError(null)
    setStarting(true)  // мгновенно показываем прогресс
    setStarted(true)   // запускаем polling
    haptic('medium')
    try {
      const resp = await api.startClusters(task.task_id)
      if (resp.state.status === 'done') {
        // Уже было рассчитано раньше — polling подхватит результат
        haptic('success')
      }
    } catch (e: any) {
      setStartError(e?.message ?? 'Не удалось запустить расчёт')
      setStarting(false)
      setStarted(false)
      haptic('error')
    }
  }

  const handleDownloadExcel = async () => {
    setExcelError(null)
    setExcelLoading(true)
    haptic('medium')
    try {
      await api.downloadClustersExcel(task.task_id)
      haptic('success')
    } catch (e: any) {
      setExcelError(e?.message ?? 'Не удалось скачать Excel')
      haptic('error')
    } finally {
      setExcelLoading(false)
    }
  }

  // === Starting (мгновенный прогресс после клика, до первого ответа API) ===
  if (starting && !data) {
    return (
      <div className="tg-card text-center py-6">
        <div className="text-3xl mb-2 animate-pulse">⏳</div>
        <div className="font-medium mb-1">Запуск расчёта...</div>
        <div className="text-xs opacity-70 mb-3">
          Загружаем границы населённых пунктов из OpenStreetMap
        </div>
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
              backgroundColor: 'var(--tg-color-button, #2481cc)',
            }}
          />
        </div>
      </div>
    )
  }

  // === Состояния ===
  if (!started && !data?.result) {
    return (
      <div className="space-y-3">
        <div className="tg-card">
          <div className="tg-section-header mb-2">Очаги концентрации ДТП</div>
          <p className="text-sm opacity-80 mb-3">
            Расчёт мест концентрации аварийности с исторической динамикой
            (сравнение с прошлым годом). Алгоритм использует границы
            населённых пунктов из OpenStreetMap и учитывает пикетаж.
          </p>
          <ul className="text-xs opacity-70 space-y-1 mb-3">
            <li>• Перекрёстки в НП (радиус 50 м)</li>
            <li>• Участки дорог в НП (окно 200 м)</li>
            <li>• Вне НП (окно 1 км)</li>
            <li>• Порог: 3+ ДТП одного вида или 5+ любых</li>
          </ul>
          <button
            onClick={handleStart}
            className="w-full py-3 rounded-xl font-medium text-sm"
            style={{
              backgroundColor: 'var(--tg-color-button, #2481cc)',
              color: 'var(--tg-color-button-text, #ffffff)',
            }}
          >
            🔥 Рассчитать очаги
          </button>
          <p className="text-xs opacity-60 mt-2 text-center">
            Занимает 15-30 секунд
          </p>
          {startError && (
            <p className="text-xs mt-2" style={{ color: '#ff3b30' }}>
              {startError}
            </p>
          )}
        </div>
      </div>
    )
  }

  // === Running ===
  if (data?.state.status === 'running' || starting) {
    const stage = data?.state.stage || 'Подготовка...'
    const progress = data?.state.progress ?? 5
    return (
      <div className="tg-card text-center py-6">
        <div className="text-3xl mb-2 animate-pulse">⏳</div>
        <div className="font-medium mb-1">Расчёт очагов...</div>
        <div className="text-xs opacity-70 mb-3">
          {stage}
        </div>
        <div
          className="w-full h-2 rounded-full overflow-hidden"
          style={{
            backgroundColor: 'var(--tg-color-secondary-bg, #f1f1f1)',
          }}
        >
          <div
            className="h-full transition-all duration-500"
            style={{
              width: `${progress}%`,
              backgroundColor: 'var(--tg-color-button, #2481cc)',
            }}
          />
        </div>
        <div className="text-xs opacity-60 mt-1">
          {progress}%
        </div>
      </div>
    )
  }

  // === Failed ===
  if (data?.state.status === 'failed') {
    return (
      <div className="tg-card">
        <div
          className="font-medium mb-2"
          style={{ color: '#ff3b30' }}
        >
          ❌ Ошибка расчёта
        </div>
        <div className="text-xs opacity-80 mb-3">
          {data.state.error ?? 'Неизвестная ошибка'}
        </div>
        <button
          onClick={handleStart}
          className="w-full py-2.5 rounded-xl font-medium text-sm"
          style={{
            backgroundColor: 'var(--tg-color-button, #2481cc)',
            color: 'var(--tg-color-button-text, #ffffff)',
          }}
        >
          Повторить расчёт
        </button>
      </div>
    )
  }

  // === Done ===
  if (data?.result) {
    const { summary, clusters, preclusters } = data.result

    // Топ-10 очагов по тяжести — ТОЛЬКО текущего периода.
    // Исключаем disappeared (is_lost) и АППГ-повторённые (is_prev_matched):
    // для них total_accidents=0, и в «текущем» топе им не место.
    const currentClusters = clusters.filter(
      (c) => !c.is_lost && !c.is_prev_matched,
    )
    const sortedClusters = [...currentClusters].sort((a, b) => {
      const sa = a.deaths * 3 + a.injured + a.total_accidents
      const sb = b.deaths * 3 + b.injured + b.total_accidents
      return sb - sa
    })

    return (
      <div className="space-y-3">
        {/* KPI-сводка */}
        <div className="tg-card">
          <div className="tg-section-header mb-3">Очаги ДТП</div>
          <div className="grid grid-cols-2 gap-2 mb-3">
            <KpiCard
              label="Всего очагов"
              value={summary.total_clusters}
              color="#ff3b30"
            />
            <KpiCard
              label="ДТП в очагах"
              value={summary.current_total_dtp}
              color="#2481cc"
            />
            <KpiCard
              label="Погибших"
              value={summary.current_deaths}
              color="#ff3b30"
            />
            <KpiCard
              label="Раненых"
              value={summary.current_injured}
              color="#ff9500"
            />
          </div>
          {summary.total_preclusters > 0 && (
            <div className="text-xs opacity-70 text-center">
              ⚠ Предочагов: <b>{summary.total_preclusters}</b> (потенциальные
              очаги следующего периода)
            </div>
          )}
          {summary.total_lost > 0 && (
            <div className="text-xs opacity-70 text-center mt-1">
              Исчезнувших очагов: <b>{summary.total_lost}</b>
            </div>
          )}
          {summary.total_prev_matched != null && summary.total_prev_matched > 0 && (
            <div className="text-xs opacity-70 text-center mt-1">
              АППГ-очагов, повторённых в текущем: <b>{summary.total_prev_matched}</b>
            </div>
          )}
        </div>

        {/* Динамика */}
        {summary.has_prev_data &&
          summary.prev_label &&
          (summary.dynamics.repeated_growing > 0 ||
            summary.dynamics.repeated_shrinking > 0 ||
            summary.dynamics.repeated_stable > 0 ||
            summary.dynamics.repeated_merged > 0 ||
            summary.dynamics.new > 0 ||
            summary.dynamics.new_with_neighbor > 0 ||
            summary.dynamics.prev_matched > 0 ||
            summary.dynamics.lost > 0 ||
            // обратная совместимость (старые ключи)
            summary.dynamics.growing > 0 ||
            summary.dynamics.shrinking > 0 ||
            summary.dynamics.stable > 0) && (
            <div className="tg-card">
              <div className="tg-section-header mb-3">
                Динамика vs {summary.prev_label}
              </div>
              <div className="space-y-1.5">
                {Object.entries(summary.dynamics).map(([key, count]) => {
                  if (count === 0) return null
                  const info = DYNAMICS_LABELS[key]
                  if (!info) return null
                  return (
                    <div
                      key={key}
                      className="flex items-center justify-between text-sm"
                    >
                      <span className="flex items-center gap-2">
                        <span style={{ color: info.color }}>{info.icon}</span>
                        <span>{info.label}</span>
                      </span>
                      <span className="font-medium">{count}</span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

        {/* Карта очагов */}
        <div className="tg-card">
          <div className="tg-section-header mb-2">Карта очагов</div>
          <p className="text-xs opacity-70 mb-2">
            Полноценная карта со слоями, попапами на каждом ДТП и очаге,
            линейкой для измерения расстояний и фильтром камер.
            Используйте кнопки в правом верхнем углу карты.
          </p>
          <div
            style={{
              borderRadius: 12,
              overflow: 'hidden',
              border: '1px solid var(--tg-color-secondary-bg, #f1f1f1)',
            }}
          >
            <iframe
              src={api.getClustersMapUrl(task.task_id)}
              style={{
                width: '100%',
                height: isTelegramDesktop() ? 700 : 450,
                border: 'none',
                display: 'block',
              }}
              title="Карта очагов ДТП"
            />
          </div>
        </div>

        {/* Excel-выгрузка */}
        <div className="tg-card">
          <div className="tg-section-header mb-2">Excel-отчёт по очагам</div>
          <p className="text-xs opacity-70 mb-3">
            Файл с 4 листами: очаги, динамика (с исчезнувшими), детализация
            всех ДТП, предочаги. Цветовое кодирование по типу зоны и статусу.
          </p>
          <button
            onClick={handleDownloadExcel}
            disabled={excelLoading}
            className="w-full py-3 rounded-xl font-medium text-sm flex items-center justify-center gap-2"
            style={{
              backgroundColor: excelLoading
                ? 'var(--tg-color-secondary-bg, #f1f1f1)'
                : 'var(--tg-color-button, #2481cc)',
              color: excelLoading
                ? 'var(--tg-color-text, #333)'
                : 'var(--tg-color-button-text, #ffffff)',
              opacity: excelLoading ? 0.6 : 1,
            }}
          >
            {excelLoading ? (
              <>
                <span className="inline-block animate-spin">⏳</span>
                Генерация Excel...
              </>
            ) : (
              <>📥 Скачать Excel (4 листа)</>
            )}
          </button>
          {excelError && (
            <p className="text-xs mt-2" style={{ color: '#ff3b30' }}>
              {excelError}
            </p>
          )}
        </div>

        {/* Топ-очаги */}
        <div className="tg-card">
          <div className="tg-section-header mb-3">
            Топ-10 очагов по тяжести
          </div>
          <div className="space-y-2">
            {sortedClusters.slice(0, 10).map((c, idx) => (
              <ClusterCard key={idx} cluster={c} index={idx + 1} />
            ))}
          </div>
        </div>

        {/* Предочаги */}
        {preclusters.length > 0 && (
          <div className="tg-card">
            <div className="tg-section-header mb-3">
              ⚠ Предочаги ({preclusters.length})
            </div>
            <p className="text-xs opacity-70 mb-2">
              Участки с ДТП ниже порога очага, но требующие внимания
              (потенциальные очаги следующего периода).
            </p>
            <div className="space-y-2">
              {preclusters.slice(0, 5).map((c, idx) => (
                <ClusterCard key={`pre-${idx}`} cluster={c} index={idx + 1} isPrecluster />
              ))}
            </div>
          </div>
        )}

        {isError && (
          <div className="text-xs text-center opacity-60">
            Не удалось обновить статус. Попробуйте обновить страницу.
          </div>
        )}
      </div>
    )
  }

  // Fallback (не должно происходить)
  return (
    <div className="tg-card text-center text-sm opacity-70">
      Загрузка...
    </div>
  )
}

// ============================================================
// Подкомпоненты
// ============================================================
function KpiCard({
  label,
  value,
  color,
}: {
  label: string
  value: number
  color: string
}) {
  return (
    <div
      className="p-2.5 rounded-xl text-center"
      style={{
        backgroundColor: 'var(--tg-color-secondary-bg, #f1f1f1)',
      }}
    >
      <div className="text-lg font-bold" style={{ color }}>
        {value}
      </div>
      <div className="text-[10px] opacity-70 leading-tight mt-0.5">
        {label}
      </div>
    </div>
  )
}

function ClusterCard({
  cluster,
  index,
  isPrecluster = false,
}: {
  cluster: ClusterItem
  index: number
  isPrecluster?: boolean
}) {
  const [expanded, setExpanded] = useState(false)
  const zoneLabel = ZONE_LABELS[cluster.zone_type] ?? cluster.zone_type
  const dynamicsInfo = cluster.dynamics?.status
    ? DYNAMICS_LABELS[cluster.dynamics.status]
    : null

  // Топ-3 вида ДТП
  const topTypes = Object.entries(cluster.type_counter)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 3)

  return (
    <div
      className="rounded-xl p-3"
      style={{
        backgroundColor: 'var(--tg-color-secondary-bg, #f1f1f1)',
        borderLeft: isPrecluster
          ? '3px solid #ff9500'
          : cluster.deaths > 0
          ? '3px solid #ff3b30'
          : '3px solid #2481cc',
      }}
    >
      <button
        onClick={() => {
          setExpanded(!expanded)
          haptic('light')
        }}
        className="w-full text-left"
      >
        <div className="flex items-start justify-between gap-2 mb-1">
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium truncate">
              {isPrecluster && '⚠ '}
              {index}. {cluster.road || 'Не указана'}
            </div>
            <div className="text-xs opacity-70">{zoneLabel}</div>
          </div>
          {dynamicsInfo && (
            <span
              className="text-xs px-1.5 py-0.5 rounded-full whitespace-nowrap"
              style={{
                backgroundColor: dynamicsInfo.color + '20',
                color: dynamicsInfo.color,
              }}
            >
              {dynamicsInfo.icon} {dynamicsInfo.label}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 text-xs">
          <span>
            ДТП: <b>{cluster.total_accidents}</b>
          </span>
          {cluster.deaths > 0 && (
            <span style={{ color: '#ff3b30' }}>
              Пог: <b>{cluster.deaths}</b>
            </span>
          )}
          {cluster.injured > 0 && (
            <span style={{ color: '#ff9500' }}>
              Ран: <b>{cluster.injured}</b>
            </span>
          )}
          {cluster.camera_match?.status && (
            <span className="text-xs opacity-70">
              📷 {cluster.camera_match.status}
            </span>
          )}
        </div>
      </button>

      {expanded && (
        <div className="mt-2 pt-2 border-t border-current/10 text-xs space-y-1">
          {cluster.dominant_type && (
            <div>
              <span className="opacity-60">Доминирующий вид:</span>{' '}
              <b>{cluster.dominant_type}</b>
            </div>
          )}
          {topTypes.length > 0 && (
            <div>
              <div className="opacity-60 mb-1">Виды ДТП:</div>
              <div className="space-y-0.5 pl-2">
                {topTypes.map(([type, count]) => (
                  <div key={type} className="flex justify-between">
                    <span className="opacity-80 truncate flex-1 mr-2">
                      {type}
                    </span>
                    <span className="font-medium">{count}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          {cluster.start_pos != null && cluster.end_pos != null && (
            <div>
              <span className="opacity-60">Пикетаж:</span>{' '}
              {cluster.start_pos.toFixed(3)} - {cluster.end_pos.toFixed(3)} км
            </div>
          )}
          {cluster.dates?.length >= 2 && (
            <div>
              <span className="opacity-60">Период:</span>{' '}
              {cluster.dates[0]} — {cluster.dates[cluster.dates.length - 1]}
            </div>
          )}
          {/* Для prev_matched: показываем, в какие текущие № он «продолжился» */}
          {cluster.dynamics?.matched_curr_numbers &&
            cluster.dynamics.matched_curr_numbers.length > 0 && (
              <div>
                <span className="opacity-60">Повторён в текущем:</span>{' '}
                <b>
                  {cluster.dynamics.matched_curr_numbers
                    .map((n: number) => `№${n}`)
                    .join(', ')}
                </b>
              </div>
            )}
          {/* Для repeated_*: показываем, какие АППГ-очаги сматчились */}
          {cluster.dynamics?.matched_prev_numbers &&
            cluster.dynamics.matched_prev_numbers.length > 0 && (
              <div>
                <span className="opacity-60">В прошлом году:</span>{' '}
                <b>
                  {cluster.dynamics.matched_prev_numbers
                    .map((n: number) => `№${n}`)
                    .join(', ')}
                </b>
              </div>
            )}
          {/* Для new_with_neighbor: показываем ближайших соседей в АППГ */}
          {cluster.dynamics?.neighbors &&
            cluster.dynamics.neighbors.length > 0 && (
              <div>
                <span className="opacity-60">Ближайшие в АППГ:</span>{' '}
                {cluster.dynamics.neighbors
                  .map(
                    (n: { prev_number: number; distance_m: number }) =>
                      `№${n.prev_number} (${Math.round(n.distance_m)}м)`,
                  )
                  .join(', ')}
              </div>
            )}
          {cluster.center && (
            <div className="opacity-60">
              📍 {cluster.center.lat.toFixed(5)}, {cluster.center.lon.toFixed(5)}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
