/**
 * Список последних задач пользователя.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { statusLabel, cn } from '@/lib/utils'
import { haptic } from '@/lib/telegram'

interface HistoryListProps {
  onSelectTask: (taskId: string) => void
}

export function HistoryList({ onSelectTask }: HistoryListProps) {
  const { data: tasks, isLoading, error } = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api.listTasks(20),
  })

  if (isLoading) {
    return (
      <div className="tg-card text-center text-sm opacity-60">
        Загрузка истории…
      </div>
    )
  }

  if (error) {
    return (
      <div className="tg-card text-center text-sm" style={{
        color: 'var(--tg-color-destructive, #ff3b30)',
      }}>
        Не удалось загрузить историю
      </div>
    )
  }

  if (!tasks || tasks.length === 0) {
    return (
      <div className="tg-card text-center text-sm opacity-60">
        История пуста. Создайте первый запрос выше.
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="tg-section-header px-1">Последние запросы</div>
      {tasks.map((task) => (
        <button
          key={task.task_id}
          onClick={() => {
            haptic('light')
            onSelectTask(task.task_id)
          }}
          className={cn(
            'tg-card w-full text-left active:opacity-70 transition-opacity'
          )}
        >
          <div className="flex items-center justify-between mb-1">
            <div className="font-medium text-sm truncate">
              {task.region_name || `Регион ${task.region_code}`}
            </div>
            <StatusBadge status={task.status} />
          </div>
          <div className="text-xs opacity-60">
            {task.period}
          </div>
        </button>
      ))}
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    done: 'var(--tg-color-button, #2481cc)',
    failed: 'var(--tg-color-destructive, #ff3b30)',
    pending: 'var(--tg-color-hint, #999999)',
    fetching: 'var(--tg-color-link, #2481cc)',
    parsing: 'var(--tg-color-link, #2481cc)',
    analytics: 'var(--tg-color-link, #2481cc)',
    generating: 'var(--tg-color-link, #2481cc)',
  }

  return (
    <span
      className="text-xs px-2 py-0.5 rounded-full whitespace-nowrap"
      style={{
        backgroundColor: colors[status] ?? 'var(--tg-color-hint, #999)',
        color: 'var(--tg-color-button-text, #ffffff)',
      }}
    >
      {statusLabel(status)}
    </span>
  )
}
