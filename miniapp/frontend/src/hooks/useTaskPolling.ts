/**
 * Хук для polling статуса задачи.
 *
 * Опрашивает /api/dtp/tasks/{id} каждые 1.5 сек, пока задача не завершится
 * (done / failed). После завершения останавливается.
 */
import { useQuery } from '@tanstack/react-query'
import { api, type TaskStatusResponse } from '@/lib/api'

const POLL_INTERVAL = 1500 // ms

export function useTaskPolling(taskId: string | null) {
  return useQuery<TaskStatusResponse>({
    queryKey: ['task', taskId],
    queryFn: () => api.getTask(taskId!),
    enabled: !!taskId,
    refetchInterval: (query) => {
      const data = query.state.data
      if (!data) return POLL_INTERVAL
      if (data.status === 'done' || data.status === 'failed') {
        return false // Останавливаем polling
      }
      return POLL_INTERVAL
    },
  })
}
