/**
 * Индикатор прогресса выполнения задачи.
 *
 * Показывает текущий этап, процент выполнения и анимированный прогресс-бар.
 */
import type { TaskStatusResponse } from '@/lib/api'
import { statusLabel } from '@/lib/utils'

interface ProgressIndicatorProps {
  task: TaskStatusResponse
}

export function ProgressIndicator({ task }: ProgressIndicatorProps) {
  const isFailed = task.status === 'failed'
  const isDone = task.status === 'done'

  return (
    <div className="tg-card">
      <div className="flex items-center justify-between mb-2">
        <div className="tg-section-header m-0">
          {task.region_name || `Регион ${task.region_code}`}
        </div>
        <div className="text-xs opacity-60">{task.period}</div>
      </div>

      {/* Прогресс-бар */}
      <div
        className="h-2 rounded-full overflow-hidden mb-2"
        style={{ backgroundColor: 'var(--tg-color-secondary-bg, #f1f1f1)' }}
      >
        <div
          className={isDone ? 'h-full transition-all duration-500' : 'h-full progress-stripes transition-all duration-500'}
          style={{
            width: `${task.progress}%`,
            backgroundColor: isFailed
              ? 'var(--tg-color-destructive, #ff3b30)'
              : 'var(--tg-color-button, #2481cc)',
          }}
        />
      </div>

      <div className="flex items-center justify-between text-xs">
        <span className={isFailed ? 'text-red-500' : ''}>
          {isFailed
            ? `Ошибка: ${task.error ?? 'неизвестная'}`
            : statusLabel(task.status)}
        </span>
        <span className="opacity-60">{task.progress}%</span>
      </div>
    </div>
  )
}
