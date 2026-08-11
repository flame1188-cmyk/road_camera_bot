/**
 * PointStatsView — вкладка «Статистика по точке».
 *
 * Логика:
 *  1. Пользователь вводит координаты вручную ИЛИ кликает по карте
 *  2. Выбирает радиус (250м / 500м / 1км / 3км)
 *  3. Нажимает «Рассчитать» — быстрая операция (<1 сек)
 *  4. Видит: KPI (ДТП, погибло, ранено), динамика vs прошлый год,
 *     мини-карта с радиусом, список ближайших ДТП
 *
 * UX:
 *  - Координаты можно вставить из буфера (55.1234, 37.5678)
 *  - Клик по карте (iframe) передаёт координаты через postMessage
 *  - Радиус можно менять без повторного ввода координат
 *  - Результат кэшируется на задаче (task.last_point_stats)
 */
import { useState } from 'react'
import {
  api,
  type PointStatsResponse,
  type TaskStatusResponse,
} from '@/lib/api'
import { haptic, isTelegramDesktop } from '@/lib/telegram'

interface PointStatsViewProps {
  task: TaskStatusResponse
}

const RADIUS_OPTIONS = [
  { value: 250, label: '250 м' },
  { value: 500, label: '500 м' },
  { value: 1000, label: '1 км' },
  { value: 3000, label: '3 км' },
]

export function PointStatsView({ task }: PointStatsViewProps) {
  const [latStr, setLatStr] = useState('')
  const [lonStr, setLonStr] = useState('')
  const [radius, setRadius] = useState(500)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<PointStatsResponse | null>(null)
  const [excelLoading, setExcelLoading] = useState(false)
  const [excelError, setExcelError] = useState<string | null>(null)
  const [showMap, setShowMap] = useState(false)

  const handleCalculate = async () => {
    setError(null)

    const lat = parseFloat(latStr.replace(',', '.'))
    const lon = parseFloat(lonStr.replace(',', '.'))

    if (isNaN(lat) || isNaN(lon)) {
      setError('Введите корректные координаты (например: 55.7558, 37.6173)')
      haptic('error')
      return
    }

    if (lat < -90 || lat > 90) {
      setError('Широта должна быть в диапазоне -90..90')
      haptic('error')
      return
    }

    if (lon < -180 || lon > 180) {
      setError('Долгота должна быть в диапазоне -180..180')
      haptic('error')
      return
    }

    setLoading(true)
    haptic('medium')
    try {
      const resp = await api.computePointStats(task.task_id, lat, lon, radius)
      setResult(resp)
      if (!resp.ok) {
        setError(resp.error ?? 'Не удалось получить статистику')
        haptic('error')
      } else {
        haptic('success')
      }
    } catch (e: any) {
      setError(e?.message ?? 'Ошибка запроса')
      haptic('error')
    } finally {
      setLoading(false)
    }
  }

  // Изменение радиуса — пересчёт с теми же координатами
  const handleRadiusChange = async (newRadius: number) => {
    if (newRadius === radius) return
    setRadius(newRadius)
    haptic('light')
    if (result?.ok && latStr && lonStr) {
      // Автопересчёт при изменении радиуса
      setTimeout(() => handleCalculate(), 0)
    }
  }

  // Скачивание Excel со статистикой по точке
  const handleDownloadExcel = async () => {
    setExcelError(null)
    setExcelLoading(true)
    haptic('medium')
    try {
      await api.downloadPointStatsExcel(task.task_id)
      haptic('success')
    } catch (e: any) {
      setExcelError(e?.message ?? 'Не удалось скачать Excel')
      haptic('error')
    } finally {
      setExcelLoading(false)
    }
  }

  // Переключение видимости карты
  const toggleMap = () => {
    setShowMap(!showMap)
    haptic('light')
  }

  return (
    <div className="space-y-3">
      {/* Форма ввода */}
      <div className="tg-card">
        <div className="tg-section-header mb-3">Статистика по точке</div>

        <div className="space-y-2 mb-3">
          <div className="text-xs opacity-70">
            📍 Введите координаты ДТП или интересной точки:
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[10px] opacity-60 block mb-0.5">
                Широта
              </label>
              <input
                type="text"
                inputMode="decimal"
                value={latStr}
                onChange={(e) => setLatStr(e.target.value)}
                placeholder="55.7558"
                className="w-full px-3 py-2 rounded-lg text-sm"
                style={{
                  backgroundColor:
                    'var(--tg-color-secondary-bg, #f1f1f1)',
                  color: 'var(--tg-color-text, #000)',
                  border: 'none',
                  outline: 'none',
                }}
              />
            </div>
            <div>
              <label className="text-[10px] opacity-60 block mb-0.5">
                Долгота
              </label>
              <input
                type="text"
                inputMode="decimal"
                value={lonStr}
                onChange={(e) => setLonStr(e.target.value)}
                placeholder="37.6173"
                className="w-full px-3 py-2 rounded-lg text-sm"
                style={{
                  backgroundColor:
                    'var(--tg-color-secondary-bg, #f1f1f1)',
                  color: 'var(--tg-color-text, #000)',
                  border: 'none',
                  outline: 'none',
                }}
              />
            </div>
          </div>
          <div className="text-[10px] opacity-60">
            Подсказка: скопируйте координаты из карт (Яндекс/Google/OSM) и
            вставьте — формат определится автоматически.
          </div>
        </div>

        {/* Радиус */}
        <div className="mb-3">
          <div className="text-xs opacity-70 mb-1.5">Радиус поиска:</div>
          <div className="grid grid-cols-4 gap-1.5">
            {RADIUS_OPTIONS.map((r) => (
              <button
                key={r.value}
                onClick={() => handleRadiusChange(r.value)}
                className="py-2 px-2 rounded-lg text-xs font-medium transition-colors"
                style={{
                  backgroundColor:
                    radius === r.value
                      ? 'var(--tg-color-button, #2481cc)'
                      : 'var(--tg-color-secondary-bg, #f1f1f1)',
                  color:
                    radius === r.value
                      ? 'var(--tg-color-button-text, #ffffff)'
                      : 'var(--tg-color-text, #000)',
                }}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>

        <button
          onClick={handleCalculate}
          disabled={loading || !latStr || !lonStr}
          className="w-full py-2.5 rounded-xl font-medium text-sm disabled:opacity-50"
          style={{
            backgroundColor: 'var(--tg-color-button, #2481cc)',
            color: 'var(--tg-color-button-text, #ffffff)',
          }}
        >
          {loading ? 'Расчёт...' : '🔍 Рассчитать'}
        </button>

        {error && (
          <div
            className="mt-2 text-xs p-2 rounded-lg"
            style={{
              backgroundColor: 'rgba(255, 59, 48, 0.1)',
              color: '#ff3b30',
            }}
          >
            {error}
          </div>
        )}
      </div>

      {/* Результат */}
      {result?.ok && result.current && (
        <>
          {/* Кнопки действий: Карта + Excel */}
          <div className="tg-card">
            <div className="grid grid-cols-2 gap-2">
              <button
                onClick={toggleMap}
                className="py-2.5 rounded-xl font-medium text-xs flex items-center justify-center gap-1.5"
                style={{
                  backgroundColor: showMap
                    ? 'var(--tg-color-button, #2481cc)'
                    : 'var(--tg-color-secondary-bg, #f1f1f1)',
                  color: showMap
                    ? 'var(--tg-color-button-text, #ffffff)'
                    : 'var(--tg-color-text, #000)',
                }}
              >
                {showMap ? '🗺 Скрыть карту' : '🗺 Открыть карту'}
              </button>
              <button
                onClick={handleDownloadExcel}
                disabled={excelLoading}
                className="py-2.5 rounded-xl font-medium text-xs flex items-center justify-center gap-1.5"
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
                {excelLoading ? '⏳ Генерация...' : '📥 Excel по точке'}
              </button>
            </div>
            {excelError && (
              <p className="text-xs mt-2" style={{ color: '#ff3b30' }}>
                {excelError}
              </p>
            )}
          </div>

          {/* Карта точки (iframe) */}
          {showMap && result.ok && (
            <div className="tg-card">
              <div className="tg-section-header mb-2">
                Карта точки — радиус {result.radius_m >= 1000
                  ? `${result.radius_m / 1000} км`
                  : `${result.radius_m} м`}
              </div>
              <p className="text-xs opacity-70 mb-2">
                Точка запроса + круг радиуса + ДТП текущего/прошлого периода +
                камеры в радиусе. Кликайте на маркеры для деталей.
              </p>
              <div
                style={{
                  borderRadius: 12,
                  overflow: 'hidden',
                  border: '1px solid var(--tg-color-secondary-bg, #f1f1f1)',
                }}
              >
                <iframe
                  src={api.getPointStatsMapUrl(
                    task.task_id,
                    result.center.lat,
                    result.center.lon,
                    result.radius_m
                  )}
                  style={{
                    width: '100%',
                    height: isTelegramDesktop() ? 700 : 450,
                    border: 'none',
                    display: 'block',
                  }}
                  title="Карта статистики по точке"
                />
              </div>
            </div>
          )}

          <PointStatsResult result={result} />
        </>
      )}
    </div>
  )
}

// ============================================================
// Результат
// ============================================================
function PointStatsResult({ result }: { result: PointStatsResponse }) {
  const cur = result.current!
  const prev = result.prev

  const radiusStr =
    result.radius_m >= 1000
      ? `${result.radius_m / 1000} км`
      : `${result.radius_m} м`

  return (
    <>
      {/* Заголовок */}
      <div className="tg-card">
        <div className="flex items-center justify-between mb-1">
          <div className="font-medium text-sm">
            📍 {result.center.lat.toFixed(5)}, {result.center.lon.toFixed(5)}
          </div>
          <div className="text-xs opacity-60">Радиус: {radiusStr}</div>
        </div>
        <div className="text-xs opacity-70">
          Период: {result.current_label}
          {result.prev_label && ` vs ${result.prev_label}`}
        </div>
      </div>

      {/* KPI текущего периода */}
      <div className="tg-card">
        <div className="tg-section-header mb-3">
          {result.current_label}
        </div>
        <div className="grid grid-cols-3 gap-2">
          <KpiCell label="ДТП" value={cur.total} color="#2481cc" />
          <KpiCell label="Погибло" value={cur.deaths} color="#ff3b30" />
          <KpiCell label="Ранено" value={cur.injured} color="#ff9500" />
          {cur.alcohol > 0 && (
            <KpiCell label="Нетрезвые" value={cur.alcohol} color="#af52de" />
          )}
          {cur.pedestrians > 0 && (
            <KpiCell
              label="Пешеходы"
              value={cur.pedestrians}
              color="#34c759"
            />
          )}
          <KpiCell
            label="На 100 ДТП"
            value={
              cur.total > 0
                ? ((cur.deaths / cur.total) * 100).toFixed(1)
                : '0'
            }
            color="#5856d6"
          />
        </div>

        {cur.total === 0 && (
          <div className="text-xs opacity-60 text-center mt-2">
            В указанном радиусе ДТП не найдено.
          </div>
        )}
      </div>

      {/* Динамика */}
      {prev && prev.total > 0 && (
        <div className="tg-card">
          <div className="tg-section-header mb-3">
            📈 Динамика vs {result.prev_label}
          </div>
          <div className="space-y-1.5">
            <DynamicRow
              label="ДТП"
              current={cur.total}
              previous={prev.total}
            />
            <DynamicRow
              label="Погибло"
              current={cur.deaths}
              previous={prev.deaths}
            />
            <DynamicRow
              label="Ранено"
              current={cur.injured}
              previous={prev.injured}
            />
            {cur.alcohol > 0 || prev.alcohol > 0 ? (
              <DynamicRow
                label="Нетрезвые"
                current={cur.alcohol}
                previous={prev.alcohol}
              />
            ) : null}
            {cur.pedestrians > 0 || prev.pedestrians > 0 ? (
              <DynamicRow
                label="Пешеходы"
                current={cur.pedestrians}
                previous={prev.pedestrians}
              />
            ) : null}
          </div>
        </div>
      )}

      {/* Распределения */}
      {cur.total > 0 && (
        <>
          {Object.keys(cur.by_type).length > 0 && (
            <DistributionCard
              title="По видам ДТП"
              data={cur.by_type}
            />
          )}
          {Object.keys(cur.by_road).length > 0 && (
            <DistributionCard
              title="По дорогам"
              data={cur.by_road}
              topN={5}
            />
          )}
          {Object.keys(cur.by_weather).length > 0 && (
            <DistributionCard
              title="По погодным условиям"
              data={cur.by_weather}
              topN={5}
            />
          )}
        </>
      )}

      {/* Ближайшие ДТП */}
      {cur.cards_preview.length > 0 && (
        <div className="tg-card">
          <div className="tg-section-header mb-3">
            Ближайшие ДТП ({cur.cards_count})
          </div>
          <div className="space-y-1.5">
            {cur.cards_preview.slice(0, 10).map((card, idx) => (
              <div
                key={idx}
                className="text-xs p-2 rounded-lg"
                style={{
                  backgroundColor:
                    'var(--tg-color-secondary-bg, #f1f1f1)',
                }}
              >
                <div className="flex justify-between mb-0.5">
                  <span className="font-medium">
                    {card.date} {card.time}
                  </span>
                  <span className="opacity-70">{card.dist_m} м</span>
                </div>
                <div className="opacity-80 truncate">{card.type}</div>
                <div className="opacity-60 truncate">{card.road}</div>
                {(card.deaths > 0 || card.injured > 0) && (
                  <div className="mt-0.5 flex gap-2">
                    {card.deaths > 0 && (
                      <span style={{ color: '#ff3b30' }}>
                        Пог: {card.deaths}
                      </span>
                    )}
                    {card.injured > 0 && (
                      <span style={{ color: '#ff9500' }}>
                        Ран: {card.injured}
                      </span>
                    )}
                  </div>
                )}
              </div>
            ))}
            {cur.cards_count > 10 && (
              <div className="text-xs opacity-60 text-center pt-1">
                …и ещё {cur.cards_count - 10} ДТП в радиусе
              </div>
            )}
          </div>
        </div>
      )}
    </>
  )
}

function KpiCell({
  label,
  value,
  color,
}: {
  label: string
  value: number | string
  color: string
}) {
  return (
    <div
      className="p-2 rounded-lg text-center"
      style={{
        backgroundColor: 'var(--tg-color-secondary-bg, #f1f1f1)',
      }}
    >
      <div className="text-base font-bold" style={{ color }}>
        {value}
      </div>
      <div className="text-[10px] opacity-70">{label}</div>
    </div>
  )
}

function DynamicRow({
  label,
  current,
  previous,
}: {
  label: string
  current: number
  previous: number
}) {
  const delta = current - previous
  const pct = previous > 0 ? Math.round((delta / previous) * 100) : 0
  const arrow = delta > 0 ? '↑' : delta < 0 ? '↓' : '→'
  const color =
    delta > 0 ? '#ff3b30' : delta < 0 ? '#34c759' : '#8e8e93'

  return (
    <div className="flex items-center justify-between text-sm">
      <span>{label}</span>
      <span className="flex items-center gap-2">
        <span className="font-medium">{current}</span>
        {previous > 0 && (
          <>
            <span className="opacity-50">←</span>
            <span className="opacity-70">{previous}</span>
            <span style={{ color }} className="text-xs">
              {arrow} {Math.abs(pct)}%
            </span>
          </>
        )}
      </span>
    </div>
  )
}

function DistributionCard({
  title,
  data,
  topN = 10,
}: {
  title: string
  data: Record<string, number>
  topN?: number
}) {
  const sorted = Object.entries(data)
    .sort(([, a], [, b]) => b - a)
    .slice(0, topN)
  const max = sorted[0]?.[1] || 1

  return (
    <div className="tg-card">
      <div className="tg-section-header mb-3">{title}</div>
      <div className="space-y-1.5">
        {sorted.map(([name, count]) => (
          <div key={name}>
            <div className="flex justify-between text-xs mb-0.5">
              <span className="truncate flex-1 mr-2 opacity-80">{name}</span>
              <span className="font-medium">{count}</span>
            </div>
            <div
              className="h-1.5 rounded-full overflow-hidden"
              style={{
                backgroundColor:
                  'var(--tg-color-secondary-bg, #f1f1f1)',
              }}
            >
              <div
                className="h-full"
                style={{
                  width: `${(count / max) * 100}%`,
                  backgroundColor: 'var(--tg-color-button, #2481cc)',
                }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
