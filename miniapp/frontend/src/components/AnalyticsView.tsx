/**
 * AnalyticsView — визуализация аналитики ДТП через Recharts.
 *
 * Структура analytics (см. analytics.build_full_analytics):
 *  {
 *    current: { total, deaths, injured, alcohol, pedestrians,
 *               deaths_per_100, injured_per_100,
 *               by_weekday, by_hour, by_type, by_type_grouped,
 *               by_weather, by_road, by_month,
 *               // severity-варианты для переключателя метрик:
 *               by_weekday_severity, by_hour_severity,
 *               by_type_grouped_severity, by_weather_severity,
 *               by_road_significance },
 *    previous: {...} | null,
 *    comparison: { total: {current, previous, change, abs_change},
 *                  deaths: {...}, injured: {...}, ...,
 *                  by_type_grouped: {current, previous},
 *                  by_road: {current, previous},
 *                  by_month: {current, previous},
 *                  by_road_significance: {current, previous},
 *                  by_weekday_severity: {current, previous},
 *                  by_hour_severity: {current, previous},
 *                  by_type_grouped_severity: {current, previous},
 *                  by_weather_severity: {current, previous} } | null,
 *    has_prev_data: boolean,
 *    prev_label: "Январь-Июнь 2025" | null,
 *    current_label: "Январь-Июнь 2026"
 *  }
 *
 * Возможности:
 *  - KPI с динамикой vs АППГ (если has_prev_data)
 *  - Переключатель метрики: ДТП / Погибшие / Раненые — применяется ко ВСЕМ графикам
 *  - График по месяцам (current vs prev)
 *  - График по значению дорог: Федеральные / Региональные / Межмуниципальные / Муниципальные / Иные
 *  - График по видам ДТП (9 канонических категорий)
 *  - График по дням недели
 *  - График по часам суток
 *  - График по погоде (сортировка по выбранной метрике)
 */
import { useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { haptic } from '@/lib/telegram'

interface AnalyticsViewProps {
  analytics: Record<string, unknown>
}

const DAY_SHORT = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']

const MONTH_ORDER = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
]

const MONTH_SHORT = [
  'Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн',
  'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек',
]

// 9 канонических категорий — порядок соответствует DTP_TYPE_ORDER в analytics.py
const DTP_TYPE_ORDER = [
  'Столкновение',
  'Наезд на пешехода',
  'Наезд на велосипедиста',
  'Наезд на стоящее ТС',
  'Съезд с дороги',
  'Опрокидывание',
  'Наезд на препятствие',
  'Наезд на лицо, использующее СИМ',
  'Иные ДТП',
]

// Короткие подписи для графика (иначе текст сливается)
const DTP_TYPE_SHORT: Record<string, string> = {
  'Столкновение': 'Столкновение',
  'Наезд на пешехода': 'Наезд на пешехода',
  'Наезд на велосипедиста': 'Наезд на велосип.',
  'Наезд на стоящее ТС': 'Наезд на стоящее ТС',
  'Съезд с дороги': 'Съезд с дороги',
  'Опрокидывание': 'Опрокидывание',
  'Наезд на препятствие': 'Наезд на препятст.',
  'Наезд на лицо, использующее СИМ': 'Наезд на СИМ',
  'Иные ДТП': 'Иные ДТП',
}

// Канонические категории значений дорог — порядок соответствует
// ROAD_SIGNIFICANCE_ORDER в analytics.py
const ROAD_SIGNIFICANCE_ORDER = [
  'Федеральные',
  'Региональные',
  'Межмуниципальные',
  'Муниципальные',
  'Иные',
]

// Метрики для переключателя — применяется ко всем визуализациям
type Metric = 'dtp' | 'deaths' | 'injured'
const METRICS: { id: Metric; label: string; color: string }[] = [
  { id: 'dtp', label: 'ДТП', color: '#2481cc' },
  { id: 'deaths', label: 'Погибшие', color: '#ff3b30' },
  { id: 'injured', label: 'Раненые', color: '#ff9500' },
]

// Структура severity-бакета: {dtp, deaths, injured}
type SeverityBucket = { dtp?: number; deaths?: number; injured?: number }

// Утилита: извлечение значения метрики из severity-бакета
function getMetricValue(
  bucket: SeverityBucket | undefined,
  metric: Metric
): number {
  if (!bucket) return 0
  if (metric === 'dtp') return bucket.dtp ?? 0
  if (metric === 'deaths') return bucket.deaths ?? 0
  return bucket.injured ?? 0
}

// Форматирование абсолютного значения динамики:
// для целых чисел — без дробной части, для дробных — 1 знак после запятой.
// Округление убирает артефакты плавающей точки вида -1.700000000000001.
function formatAbsValue(abs: number): string {
  if (Number.isInteger(abs)) return String(abs)
  const rounded = Math.round(abs * 10) / 10
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1)
}

// Форматирование динамики для KPI
function formatDelta(
  current: number,
  previous: number | undefined
): { text: string; color: string } | null {
  if (previous === undefined || previous === null) return null
  const abs = current - previous
  if (previous === 0) {
    if (current === 0) return { text: '0% →', color: '#8e8e93' }
    return { text: 'новое ↑', color: '#ff3b30' }
  }
  const pct = (abs / previous) * 100
  const arrow = abs > 0 ? '↑' : abs < 0 ? '↓' : '→'
  const color = abs > 0 ? '#ff3b30' : abs < 0 ? '#34c759' : '#8e8e93'
  const sign = abs > 0 ? '+' : ''
  return {
    text: `${sign}${formatAbsValue(abs)} (${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%) ${arrow}`,
    color,
  }
}

// ============================================================
// Tooltip-форматтеры (используются во всех бар-чартах)
// ============================================================
function makeBarTooltipFormatter(
  currentLabel: string,
  prevLabel: string
) {
  return (value: any, name: any): [any, string] => [
    value,
    name === 'current' ? currentLabel : prevLabel,
  ]
}

function makeBarLegendFormatter(
  currentLabel: string,
  prevLabel: string
) {
  return (value: string): string =>
    value === 'current' ? currentLabel : prevLabel
}

// ============================================================
// Главный компонент
// ============================================================
export function AnalyticsView({ analytics }: AnalyticsViewProps) {
  const a = analytics as Record<string, any>
  const [metric, setMetric] = useState<Metric>('dtp')

  // === Извлекаем current/previous ===
  const current = (a.current ?? a) as Record<string, any>
  const previous = (a.previous ?? null) as Record<string, any> | null
  const hasPrev = !!a.has_prev_data && !!previous
  const prevLabel = (a.prev_label ?? 'АППГ') as string
  const currentLabel = (a.current_label ?? 'Текущий период') as string

  // Текущая метрика для подписи
  const currentMetricLabel =
    METRICS.find((m) => m.id === metric)?.label ?? 'ДТП'

  // === KPI с динамикой ===
  const kpiItems = [
    { key: 'total', label: 'Всего ДТП', value: current.total, color: '#2481cc' },
    { key: 'deaths', label: 'Погибших', value: current.deaths, color: '#ff3b30' },
    { key: 'injured', label: 'Раненых', value: current.injured, color: '#ff9500' },
    { key: 'alcohol', label: 'Нетрезвые', value: current.alcohol, color: '#af52de' },
    { key: 'pedestrians', label: 'Пешеходы', value: current.pedestrians, color: '#34c759' },
    {
      key: 'deaths_per_100',
      label: 'Погибших / 100',
      value:
        typeof current.deaths_per_100 === 'number'
          ? current.deaths_per_100.toFixed(1)
          : '—',
      color: '#5856d6',
    },
  ]

  // === Данные для графиков ===

  // По дням недели (severity-вариант)
  const weekdayCur = (current.by_weekday_severity ?? {}) as Record<string, SeverityBucket>
  const weekdayPrev = (previous?.by_weekday_severity ?? {}) as Record<string, SeverityBucket>
  const weekdayData = Array.from({ length: 7 }, (_, i) => {
    const k = String(i)
    return {
      day: DAY_SHORT[i],
      current: getMetricValue(weekdayCur[k], metric),
      previous: hasPrev ? getMetricValue(weekdayPrev[k], metric) : undefined,
    }
  }).filter((d) => d.current > 0 || (d.previous ?? 0) > 0)

  // По часам (severity-вариант)
  const hourCur = (current.by_hour_severity ?? {}) as Record<string, SeverityBucket>
  const hourPrev = (previous?.by_hour_severity ?? {}) as Record<string, SeverityBucket>
  const hourData = Array.from({ length: 24 }, (_, h) => {
    const k = String(h)
    return {
      hour: `${h}`,
      current: getMetricValue(hourCur[k], metric),
      previous: hasPrev ? getMetricValue(hourPrev[k], metric) : undefined,
    }
  })

  // По видам ДТП (9 категорий, severity-вариант)
  const typeCur = (current.by_type_grouped_severity ?? {}) as Record<string, SeverityBucket>
  const typePrev = (previous?.by_type_grouped_severity ?? {}) as Record<string, SeverityBucket>
  const typeData = DTP_TYPE_ORDER.filter(
    (t) =>
      (typeCur[t]?.dtp ?? 0) > 0 || (typePrev[t]?.dtp ?? 0) > 0
  ).map((t) => ({
    name: DTP_TYPE_SHORT[t] ?? t,
    fullName: t,
    current: getMetricValue(typeCur[t], metric),
    previous: hasPrev ? getMetricValue(typePrev[t], metric) : undefined,
  }))

  // По погоде (severity-вариант) — сортировка по выбранной метрике
  const weatherCur = (current.by_weather_severity ?? {}) as Record<string, SeverityBucket>
  const weatherPrev = (previous?.by_weather_severity ?? {}) as Record<string, SeverityBucket>
  const weatherKeys = Array.from(
    new Set([...Object.keys(weatherCur), ...Object.keys(weatherPrev)])
  )
  const weatherData = weatherKeys
    .map((w) => ({
      name: w,
      current: getMetricValue(weatherCur[w], metric),
      previous: hasPrev ? getMetricValue(weatherPrev[w], metric) : undefined,
    }))
    .filter((d) => d.current > 0 || (d.previous ?? 0) > 0)
    .sort((a, b) => b.current - a.current)
    .slice(0, 10)

  // По месяцам (severity-вариант) — структура уже {dtp, deaths, injured}
  const byMonthCurrent = (current.by_month ?? {}) as Record<string, SeverityBucket>
  const byMonthPrev = (previous?.by_month ?? {}) as Record<string, SeverityBucket>
  const monthData = MONTH_ORDER.filter(
    (m) => byMonthCurrent[m] || byMonthPrev[m]
  ).map((m) => ({
    month: MONTH_SHORT[MONTH_ORDER.indexOf(m)],
    fullMonth: m,
    current: getMetricValue(byMonthCurrent[m], metric),
    previous: hasPrev ? getMetricValue(byMonthPrev[m], metric) : undefined,
  }))

  // По значению дорог (Федеральные / Региональные / Муниципальные / ...)
  const roadSigCur = (current.by_road_significance ?? {}) as Record<string, SeverityBucket>
  const roadSigPrev = (previous?.by_road_significance ?? {}) as Record<string, SeverityBucket>
  const roadSigData = ROAD_SIGNIFICANCE_ORDER.filter(
    (cat) =>
      (roadSigCur[cat]?.dtp ?? 0) > 0 || (roadSigPrev[cat]?.dtp ?? 0) > 0
  ).map((cat) => ({
    name: cat,
    current: getMetricValue(roadSigCur[cat], metric),
    previous: hasPrev ? getMetricValue(roadSigPrev[cat], metric) : undefined,
  }))

  // Форматтеры
  const tooltipFormatter = makeBarTooltipFormatter(currentLabel, prevLabel)
  const legendFormatter = makeBarLegendFormatter(currentLabel, prevLabel)

  return (
    <div className="space-y-3">
      {/* === KPI-сводка с динамикой === */}
      <div className="tg-card">
        <div className="tg-section-header mb-3">
          Сводка {hasPrev && `vs ${prevLabel}`}
        </div>
        <div className="grid grid-cols-3 gap-2">
          {kpiItems.map((kpi) => {
            const prevValue = hasPrev
              ? Number(previous[kpi.key] ?? 0)
              : undefined
            const curValue = Number(kpi.value)
            const delta = hasPrev ? formatDelta(curValue, prevValue) : null
            return (
              <div
                key={kpi.label}
                className="p-2.5 rounded-xl text-center"
                style={{
                  backgroundColor:
                    'var(--tg-color-secondary-bg, #f1f1f1)',
                }}
              >
                <div
                  className="text-lg font-bold"
                  style={{ color: kpi.color }}
                >
                  {kpi.value}
                </div>
                <div className="text-[10px] opacity-70 leading-tight mt-0.5">
                  {kpi.label}
                </div>
                {delta && (
                  <div
                    className="text-[10px] mt-1 font-medium leading-tight"
                    style={{ color: delta.color }}
                  >
                    {delta.text}
                  </div>
                )}
              </div>
            )
          })}
        </div>
        {typeof current.injured_per_100 === 'number' && (
          <div className="mt-2 text-xs opacity-60 text-center">
            Раненых на 100 ДТП:{' '}
            <b>{current.injured_per_100.toFixed(1)}</b>
            {hasPrev && typeof previous?.injured_per_100 === 'number' && (
              <span
                style={{
                  color:
                    previous.injured_per_100 > current.injured_per_100
                      ? '#34c759'
                      : previous.injured_per_100 < current.injured_per_100
                      ? '#ff3b30'
                      : '#8e8e93',
                  marginLeft: 6,
                }}
              >
                ({previous.injured_per_100.toFixed(1)} в АППГ)
              </span>
            )}
          </div>
        )}
      </div>

      {/* === Переключатель метрики — применяется ко всем графикам ниже === */}
      <div className="tg-card">
        <div className="tg-section-header mb-2">Метрика визуализаций</div>
        <div className="grid grid-cols-3 gap-1.5">
          {METRICS.map((m) => (
            <button
              key={m.id}
              onClick={() => {
                setMetric(m.id)
                haptic('light')
              }}
              className="py-2 px-2 rounded-lg text-xs font-medium transition-colors"
              style={{
                backgroundColor:
                  metric === m.id
                    ? m.color
                    : 'var(--tg-color-secondary-bg, #f1f1f1)',
                color:
                  metric === m.id
                    ? '#ffffff'
                    : 'var(--tg-color-text, #000000)',
              }}
            >
              {m.label}
            </button>
          ))}
        </div>
        <div className="text-[10px] opacity-60 mt-1.5 text-center">
          Все графики ниже отображают выбранную метрику
        </div>
      </div>

      {/* === Динамика по месяцам vs АППГ === */}
      {monthData.length > 0 && (
        <div className="tg-card">
          <div className="tg-section-header mb-3">
            Динамика по месяцам ({currentMetricLabel})
            {hasPrev && ` vs ${prevLabel}`}
          </div>
          <div style={{ width: '100%', height: 240 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart
                data={monthData}
                margin={{ top: 5, right: 10, bottom: 5, left: -15 }}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="var(--tg-color-hint, #ccc)"
                  strokeOpacity={0.3}
                />
                <XAxis
                  dataKey="month"
                  tick={{
                    fontSize: 11,
                    fill: 'var(--tg-color-hint, #999)',
                  }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tick={{
                    fontSize: 10,
                    fill: 'var(--tg-color-hint, #999)',
                  }}
                  axisLine={false}
                  tickLine={false}
                  width={35}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor:
                      'var(--tg-color-section-bg, #fff)',
                    border: 'none',
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  labelFormatter={(l, payload) => {
                    const p = payload?.[0]?.payload
                    return p?.fullMonth ?? l
                  }}
                  formatter={(value: any, name: any) => [
                    value,
                    name === 'current' ? currentLabel : prevLabel,
                  ]}
                />
                {hasPrev && (
                  <Legend
                    wrapperStyle={{ fontSize: 11 }}
                    formatter={legendFormatter}
                  />
                )}
                <Line
                  type="monotone"
                  dataKey="current"
                  stroke="#2481cc"
                  strokeWidth={2.5}
                  dot={{ r: 3, fill: '#2481cc' }}
                  activeDot={{ r: 5 }}
                />
                {hasPrev && (
                  <Line
                    type="monotone"
                    dataKey="previous"
                    stroke="#ff9500"
                    strokeWidth={2}
                    strokeDasharray="5 3"
                    dot={{ r: 2, fill: '#ff9500' }}
                  />
                )}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* === По значению дорог: Федеральные / Региональные / Муниципальные === */}
      {roadSigData.length > 0 && (
        <div className="tg-card">
          <div className="tg-section-header mb-3">
            Аварийность по значению дорог ({currentMetricLabel})
            {hasPrev && ` vs ${prevLabel}`}
          </div>
          <div
            style={{
              width: '100%',
              height: Math.max(180, roadSigData.length * 36 + 30),
            }}
          >
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                layout="vertical"
                data={roadSigData}
                margin={{ top: 5, right: 15, bottom: 5, left: 5 }}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="var(--tg-color-hint, #ccc)"
                  strokeOpacity={0.3}
                  horizontal={false}
                />
                <XAxis
                  type="number"
                  tick={{
                    fontSize: 10,
                    fill: 'var(--tg-color-hint, #999)',
                  }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  type="category"
                  dataKey="name"
                  tick={{
                    fontSize: 11,
                    fill: 'var(--tg-color-text, #000)',
                  }}
                  axisLine={false}
                  tickLine={false}
                  width={120}
                />
                <Tooltip
                  cursor={{ fill: 'rgba(0,0,0,0.05)' }}
                  contentStyle={{
                    backgroundColor:
                      'var(--tg-color-section-bg, #fff)',
                    border: 'none',
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  formatter={tooltipFormatter}
                />
                {hasPrev && (
                  <Legend
                    wrapperStyle={{ fontSize: 11 }}
                    formatter={legendFormatter}
                  />
                )}
                <Bar
                  dataKey="current"
                  fill="#2481cc"
                  radius={[0, 4, 4, 0]}
                  barSize={hasPrev ? 12 : 18}
                />
                {hasPrev && (
                  <Bar
                    dataKey="previous"
                    fill="#ff9500"
                    radius={[0, 4, 4, 0]}
                    barSize={12}
                  />
                )}
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* === По видам ДТП (9 категорий) === */}
      {typeData.length > 0 && (
        <div className="tg-card">
          <div className="tg-section-header mb-3">
            По видам ДТП ({currentMetricLabel})
            {hasPrev && ` vs ${prevLabel}`}
          </div>
          <div
            style={{
              width: '100%',
              height: Math.max(240, typeData.length * 32 + 30),
            }}
          >
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                layout="vertical"
                data={typeData}
                margin={{ top: 5, right: 15, bottom: 5, left: 5 }}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="var(--tg-color-hint, #ccc)"
                  strokeOpacity={0.3}
                  horizontal={false}
                />
                <XAxis
                  type="number"
                  tick={{
                    fontSize: 10,
                    fill: 'var(--tg-color-hint, #999)',
                  }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  type="category"
                  dataKey="name"
                  tick={{
                    fontSize: 11,
                    fill: 'var(--tg-color-text, #000)',
                  }}
                  axisLine={false}
                  tickLine={false}
                  width={150}
                />
                <Tooltip
                  cursor={{ fill: 'rgba(0,0,0,0.05)' }}
                  contentStyle={{
                    backgroundColor:
                      'var(--tg-color-section-bg, #fff)',
                    border: 'none',
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  formatter={tooltipFormatter}
                  labelFormatter={(_, payload) =>
                    payload?.[0]?.payload?.fullName ?? ''
                  }
                />
                {hasPrev && (
                  <Legend
                    wrapperStyle={{ fontSize: 11 }}
                    formatter={legendFormatter}
                  />
                )}
                <Bar
                  dataKey="current"
                  fill="#2481cc"
                  radius={[0, 4, 4, 0]}
                  barSize={hasPrev ? 10 : 16}
                />
                {hasPrev && (
                  <Bar
                    dataKey="previous"
                    fill="#ff9500"
                    radius={[0, 4, 4, 0]}
                    barSize={10}
                  />
                )}
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* === По дням недели === */}
      {weekdayData.length > 0 && (
        <div className="tg-card">
          <div className="tg-section-header mb-3">
            По дням недели ({currentMetricLabel})
            {hasPrev && ` vs ${prevLabel}`}
          </div>
          <div style={{ width: '100%', height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={weekdayData}
                margin={{ top: 5, right: 5, bottom: 5, left: -20 }}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="var(--tg-color-hint, #ccc)"
                  strokeOpacity={0.3}
                />
                <XAxis
                  dataKey="day"
                  tick={{
                    fontSize: 11,
                    fill: 'var(--tg-color-hint, #999)',
                  }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tick={{
                    fontSize: 10,
                    fill: 'var(--tg-color-hint, #999)',
                  }}
                  axisLine={false}
                  tickLine={false}
                  width={35}
                />
                <Tooltip
                  cursor={{ fill: 'rgba(0,0,0,0.05)' }}
                  contentStyle={{
                    backgroundColor:
                      'var(--tg-color-section-bg, #fff)',
                    border: 'none',
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  formatter={tooltipFormatter}
                />
                {hasPrev && (
                  <Legend
                    wrapperStyle={{ fontSize: 11 }}
                    formatter={legendFormatter}
                  />
                )}
                <Bar
                  dataKey="current"
                  fill="#2481cc"
                  radius={[4, 4, 0, 0]}
                  barSize={hasPrev ? 10 : 18}
                />
                {hasPrev && (
                  <Bar
                    dataKey="previous"
                    fill="#ff9500"
                    radius={[4, 4, 0, 0]}
                    barSize={10}
                  />
                )}
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* === По часам суток === */}
      {hourData.some((d) => d.current > 0 || (d.previous ?? 0) > 0) && (
        <div className="tg-card">
          <div className="tg-section-header mb-3">
            По часам суток ({currentMetricLabel})
            {hasPrev && ` vs ${prevLabel}`}
          </div>
          <div style={{ width: '100%', height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart
                data={hourData}
                margin={{ top: 5, right: 5, bottom: 5, left: -20 }}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="var(--tg-color-hint, #ccc)"
                  strokeOpacity={0.3}
                />
                <XAxis
                  dataKey="hour"
                  tick={{
                    fontSize: 10,
                    fill: 'var(--tg-color-hint, #999)',
                  }}
                  axisLine={false}
                  tickLine={false}
                  interval={2}
                />
                <YAxis
                  tick={{
                    fontSize: 10,
                    fill: 'var(--tg-color-hint, #999)',
                  }}
                  axisLine={false}
                  tickLine={false}
                  width={35}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor:
                      'var(--tg-color-section-bg, #fff)',
                    border: 'none',
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  formatter={tooltipFormatter}
                  labelFormatter={(l) => `${l}:00`}
                />
                {hasPrev && (
                  <Legend
                    wrapperStyle={{ fontSize: 11 }}
                    formatter={legendFormatter}
                  />
                )}
                <Line
                  type="monotone"
                  dataKey="current"
                  stroke="#2481cc"
                  strokeWidth={2}
                  dot={{ r: 2, fill: '#2481cc' }}
                  activeDot={{ r: 4 }}
                />
                {hasPrev && (
                  <Line
                    type="monotone"
                    dataKey="previous"
                    stroke="#ff9500"
                    strokeWidth={2}
                    strokeDasharray="5 3"
                    dot={false}
                  />
                )}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* === По погоде (топ-10 по выбранной метрике) === */}
      {weatherData.length > 0 && (
        <div className="tg-card">
          <div className="tg-section-header mb-3">
            По погоде ({currentMetricLabel}, топ-{weatherData.length})
            {hasPrev && ` vs ${prevLabel}`}
          </div>
          <div
            style={{
              width: '100%',
              height: Math.max(200, weatherData.length * 32 + 30),
            }}
          >
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                layout="vertical"
                data={weatherData}
                margin={{ top: 5, right: 15, bottom: 5, left: 5 }}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="var(--tg-color-hint, #ccc)"
                  strokeOpacity={0.3}
                  horizontal={false}
                />
                <XAxis
                  type="number"
                  tick={{
                    fontSize: 10,
                    fill: 'var(--tg-color-hint, #999)',
                  }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  type="category"
                  dataKey="name"
                  tick={{
                    fontSize: 10,
                    fill: 'var(--tg-color-text, #000)',
                  }}
                  axisLine={false}
                  tickLine={false}
                  width={140}
                  tickFormatter={(v) =>
                    String(v).length > 22
                      ? String(v).slice(0, 22) + '…'
                      : v
                  }
                />
                <Tooltip
                  cursor={{ fill: 'rgba(0,0,0,0.05)' }}
                  contentStyle={{
                    backgroundColor:
                      'var(--tg-color-section-bg, #fff)',
                    border: 'none',
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  formatter={tooltipFormatter}
                />
                {hasPrev && (
                  <Legend
                    wrapperStyle={{ fontSize: 11 }}
                    formatter={legendFormatter}
                  />
                )}
                <Bar
                  dataKey="current"
                  fill="#2481cc"
                  radius={[0, 4, 4, 0]}
                  barSize={hasPrev ? 10 : 16}
                />
                {hasPrev && (
                  <Bar
                    dataKey="previous"
                    fill="#ff9500"
                    radius={[0, 4, 4, 0]}
                    barSize={10}
                  />
                )}
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  )
}
