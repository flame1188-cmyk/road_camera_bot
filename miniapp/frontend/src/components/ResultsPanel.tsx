/**
 * Панель результатов: показывает готовые файлы, HTML-карту и аналитику.
 */
import { useState } from 'react'
import { api, type TaskStatusResponse } from '@/lib/api'
import { haptic } from '@/lib/telegram'
import { formatSize, statusLabel } from '@/lib/utils'
import { MapFrame } from './MapFrame'
import { AnalyticsView } from './AnalyticsView'
import { ClustersView } from './ClustersView'
import { PointStatsView } from './PointStatsView'
import { LLMAnalysisView } from './LLMAnalysisView'

interface ResultsPanelProps {
  task: TaskStatusResponse
}

type Tab = 'map' | 'analytics' | 'clusters' | 'point' | 'llm' | 'files'

export function ResultsPanel({ task }: ResultsPanelProps) {
  const [tab, setTab] = useState<Tab>('map')

  const cardsFile = task.files.find((f) => f.type === 'dtp_cards')
  const uchFile = task.files.find((f) => f.type === 'dtp_participants')
  const mapFile = task.files.find((f) => f.type === 'map_html')

  const tabs: { id: Tab; label: string; visible: boolean }[] = [
    { id: 'map', label: 'Карта', visible: !!mapFile },
    { id: 'analytics', label: 'Аналитика', visible: !!task.analytics },
    { id: 'clusters', label: 'Очаги', visible: true },
    { id: 'point', label: 'По точке', visible: true },
    { id: 'llm', label: 'ИИ-анализ', visible: true },
    { id: 'files', label: 'Файлы', visible: task.files.length > 0 },
  ]
  const visibleTabs = tabs.filter((t) => t.visible)

  return (
    <div className="space-y-3">
      {/* Заголовок задачи */}
      <div className="tg-card">
        <div className="flex items-center justify-between mb-1">
          <div className="font-semibold">
            {task.region_name || `Регион ${task.region_code}`}
          </div>
          <div
            className="text-xs px-2 py-0.5 rounded-full"
            style={{
              backgroundColor: 'var(--tg-color-secondary-bg, #f1f1f1)',
              color: 'var(--tg-color-text, #000000)',
            }}
          >
            {statusLabel(task.status)}
          </div>
        </div>
        <div className="text-xs opacity-60">Период: {task.period}</div>
      </div>

      {/* Табы */}
      {visibleTabs.length > 0 && (
        <div className="flex gap-1 p-1 rounded-xl overflow-x-auto" style={{
          backgroundColor: 'var(--tg-color-secondary-bg, #f1f1f1)',
        }}>
          {visibleTabs.map((t) => (
            <button
              key={t.id}
              onClick={() => {
                setTab(t.id)
                haptic('light')
              }}
              className="flex-1 min-w-[70px] py-2 px-2 text-xs font-medium rounded-lg transition-colors whitespace-nowrap"
              style={{
                backgroundColor:
                  tab === t.id
                    ? 'var(--tg-color-section-bg, #ffffff)'
                    : 'transparent',
                color:
                  tab === t.id
                    ? 'var(--tg-color-button, #2481cc)'
                    : 'var(--tg-color-hint, #999999)',
              }}
            >
              {t.label}
            </button>
          ))}
        </div>
      )}

      {/* Содержимое таба */}
      {tab === 'map' && mapFile && <MapFrame taskId={task.task_id} />}

      {tab === 'analytics' && task.analytics && (
        <AnalyticsView analytics={task.analytics} />
      )}

      {tab === 'clusters' && <ClustersView task={task} />}

      {tab === 'point' && <PointStatsView task={task} />}

      {tab === 'llm' && <LLMAnalysisView task={task} />}

      {tab === 'files' && (
        <FilesList
          task={task}
          cardsFile={cardsFile}
          uchFile={uchFile}
          mapFile={mapFile}
        />
      )}
    </div>
  )
}

// ============================================================
// Список файлов
// ============================================================
interface FilesListProps {
  task: TaskStatusResponse
  cardsFile?: { type: string; filename: string; size_bytes: number; mime: string }
  uchFile?: { type: string; filename: string; size_bytes: number; mime: string }
  mapFile?: { type: string; filename: string; size_bytes: number; mime: string }
}

function FilesList({ task, cardsFile, uchFile, mapFile }: FilesListProps) {
  const files = [cardsFile, uchFile, mapFile].filter(Boolean) as {
    type: string
    filename: string
    size_bytes: number
    mime: string
  }[]

  const typeLabels: Record<string, string> = {
    dtp_cards: 'Карточки ДТП (Excel)',
    dtp_participants: 'Участники ДТП (Excel)',
    map_html: 'Карта (HTML)',
  }

  return (
    <div className="space-y-2">
      {files.map((file) => (
        <a
          key={file.type}
          href={api.getDownloadUrl(task.task_id, file.type)}
          onClick={() => haptic('medium')}
          className="tg-card flex items-center justify-between active:opacity-70"
          style={{ textDecoration: 'none' }}
        >
          <div className="flex-1 min-w-0">
            <div className="font-medium text-sm truncate">
              {typeLabels[file.type] ?? file.type}
            </div>
            <div className="text-xs opacity-60 truncate">
              {file.filename} · {formatSize(file.size_bytes)}
            </div>
          </div>
          <div
            className="ml-3 px-3 py-1.5 rounded-lg text-xs font-medium"
            style={{
              backgroundColor: 'var(--tg-color-button, #2481cc)',
              color: 'var(--tg-color-button-text, #ffffff)',
            }}
          >
            Скачать
          </div>
        </a>
      ))}
    </div>
  )
}
