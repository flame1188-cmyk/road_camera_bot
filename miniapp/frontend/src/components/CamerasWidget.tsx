/**
 * CamerasWidget — управление камерами фотовидеофиксации.
 *
 * Показывает:
 *  - список загруженных регионов (с количеством камер и датой)
 *  - форму загрузки .xls для выбранного региона
 *  - кнопку удаления файла камер
 *
 * Файлы камер хранятся в data/cameras_{reg_code}.xls на сервере.
 * При построении карты они автоматически подгружаются для соответствующего региона.
 */
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type CameraRegionInfo, type Region } from '@/lib/api'
import { haptic, showAlert } from '@/lib/telegram'
import { formatSize } from '@/lib/utils'

const QK_CAMERAS = ['cameras'] as const

export function CamerasWidget() {
  const queryClient = useQueryClient()
  const [expanded, setExpanded] = useState(false)

  // === Список загруженных камер ===
  const listQuery = useQuery({
    queryKey: QK_CAMERAS,
    queryFn: api.listCameras,
    staleTime: 30 * 1000,
  })

  const regionsQuery = useQuery({
    queryKey: ['regions'],
    queryFn: api.listRegions,
    staleTime: 5 * 60 * 1000,
  })

  // === Мутация удаления ===
  const deleteMutation = useMutation({
    mutationFn: (regCode: string) => api.deleteCameras(regCode),
    onSuccess: () => {
      haptic('success')
      queryClient.invalidateQueries({ queryKey: QK_CAMERAS })
    },
    onError: async (err: Error) => {
      haptic('error')
      await showAlert(`Не удалось удалить:\n${err.message}`)
    },
  })

  const regions: CameraRegionInfo[] = listQuery.data?.regions ?? []
  const totalCount = regions.length

  return (
    <div className="tg-card">
      <button
        type="button"
        onClick={() => {
          haptic('light')
          setExpanded((v) => !v)
        }}
        className="w-full flex items-center justify-between"
      >
        <div className="flex items-center gap-2">
          <span className="tg-section-header text-left">Камеры фотовидеофиксации</span>
          {totalCount > 0 && (
            <span
              className="text-xs px-2 py-0.5 rounded-full"
              style={{
                backgroundColor: 'var(--tg-color-button, #2481cc)',
                color: 'var(--tg-color-button-text, #ffffff)',
              }}
            >
              {totalCount}
            </span>
          )}
        </div>
        <span className="text-xs opacity-50">{expanded ? '▲' : '▼'}</span>
      </button>

      {expanded && (
        <div className="mt-3 space-y-3">
          <p className="text-xs opacity-60">
            Файлы камер загружаются для региона и автоматически используются
            при построении карты. Источник:{' '}
            <a
              href="https://gibddrf.com/camera/"
              target="_blank"
              rel="noreferrer"
              style={{ color: 'var(--tg-color-link, #2481cc)' }}
            >
              gibddrf.com/camera
            </a>
            . Формат: <code>gibddrf_cameras_change_*.xls(x)</code>.
          </p>

          {/* === Загрузка нового файла === */}
          <UploadForm
            regions={regionsQuery.data ?? []}
            onUploaded={() => {
              queryClient.invalidateQueries({ queryKey: QK_CAMERAS })
            }}
          />

          {/* === Список загруженных === */}
          <div>
            <div className="text-xs opacity-60 mb-1.5">
              Загруженные регионы
            </div>
            {listQuery.isLoading && (
              <div className="text-xs opacity-50">Загружаем…</div>
            )}
            {listQuery.isError && (
              <div
                className="text-xs"
                style={{ color: 'var(--tg-color-destructive, #ff3b30)' }}
              >
                Не удалось получить список.
              </div>
            )}
            {regions.length === 0 && !listQuery.isLoading && (
              <div className="text-xs opacity-50 py-2 text-center">
                Нет загруженных файлов камер.
              </div>
            )}
            <div className="space-y-1.5">
              {regions.map((r) => (
                <RegionRow
                  key={r.reg_code}
                  info={r}
                  onDelete={() => deleteMutation.mutate(r.reg_code)}
                  isDeleting={
                    deleteMutation.isPending &&
                    deleteMutation.variables === r.reg_code
                  }
                />
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ============================================================
// Загрузка файла
// ============================================================
interface UploadFormProps {
  regions: Region[]
  onUploaded: () => void
}

function UploadForm({ regions, onUploaded }: UploadFormProps) {
  const [selectedRegion, setSelectedRegion] = useState<Region | null>(null)
  const [search, setSearch] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState<string | null>(null)

  const uploadMutation = useMutation({
    mutationFn: () => {
      if (!selectedRegion) throw new Error('Выберите регион')
      if (!file) throw new Error('Выберите файл')
      return api.uploadCameras(selectedRegion.code, file)
    },
    onSuccess: async (data) => {
      haptic('success')
      await showAlert(
        `${data.message}\n\nРегион: ${selectedRegion?.name}\nРазмер: ${formatSize(
          data.file_size_bytes
        )}`
      )
      setFile(null)
      setSelectedRegion(null)
      setError(null)
      onUploaded()
    },
    onError: async (err: Error) => {
      haptic('error')
      setError(err.message)
    },
  })

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return regions
    return regions.filter(
      (r) => r.name.toLowerCase().includes(q) || r.code.includes(q)
    )
  }, [regions, search])

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) {
      setFile(null)
      return
    }
    // Проверяем расширение
    const name = f.name.toLowerCase()
    if (!name.endsWith('.xls') && !name.endsWith('.xlsx')) {
      haptic('error')
      setError('Файл должен быть .xls или .xlsx')
      setFile(null)
      return
    }
    setError(null)
    setFile(f)
    haptic('light')
  }

  return (
    <div
      className="p-3 rounded-xl space-y-2"
      style={{ backgroundColor: 'var(--tg-color-secondary-bg, #f1f1f1)' }}
    >
      <div className="text-xs font-medium opacity-80">Загрузить новый файл</div>

      {/* Выбор региона */}
      {selectedRegion ? (
        <button
          type="button"
          onClick={() => setSelectedRegion(null)}
          className="w-full text-left p-2 rounded-lg text-sm"
          style={{ backgroundColor: 'var(--tg-color-section-bg, #ffffff)' }}
        >
          <div className="font-medium">{selectedRegion.name}</div>
          <div className="text-xs opacity-50">код {selectedRegion.code} · изменить</div>
        </button>
      ) : (
        <div>
          <input
            type="text"
            placeholder="Найти регион…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="tg-input"
            disabled={uploadMutation.isPending}
          />
          {filtered.length > 0 && (
            <div
              className="mt-1 rounded-lg overflow-hidden max-h-36 overflow-y-auto"
              style={{ backgroundColor: 'var(--tg-color-section-bg, #ffffff)' }}
            >
              {filtered.slice(0, 10).map((r) => (
                <button
                  key={r.code}
                  type="button"
                  onClick={() => {
                    haptic('light')
                    setSelectedRegion(r)
                    setSearch('')
                  }}
                  disabled={uploadMutation.isPending}
                  className="w-full text-left px-2.5 py-2 text-sm active:opacity-70"
                  style={{ borderBottom: '1px solid rgba(0,0,0,0.05)' }}
                >
                  {r.name}{' '}
                  <span className="text-xs opacity-50">· {r.code}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Выбор файла */}
      <label className="block">
        <input
          type="file"
          accept=".xls,.xlsx"
          onChange={handleFileChange}
          disabled={uploadMutation.isPending || !selectedRegion}
          className="hidden"
        />
        <span
          className="block text-center text-xs py-2 rounded-lg cursor-pointer"
          style={{
            backgroundColor: 'var(--tg-color-section-bg, #ffffff)',
            opacity: !selectedRegion || uploadMutation.isPending ? 0.5 : 1,
          }}
        >
          {file ? `📄 ${file.name}` : '📎 Выбрать .xls файл'}
        </span>
      </label>

      {error && (
        <div
          className="text-xs"
          style={{ color: 'var(--tg-color-destructive, #ff3b30)' }}
        >
          {error}
        </div>
      )}

      {/* Кнопка загрузки */}
      <button
        type="button"
        onClick={() => uploadMutation.mutate()}
        disabled={
          uploadMutation.isPending || !selectedRegion || !file
        }
        className="w-full py-2 rounded-lg text-sm font-medium"
        style={{
          backgroundColor: 'var(--tg-color-button, #2481cc)',
          color: 'var(--tg-color-button-text, #ffffff)',
          opacity:
            uploadMutation.isPending || !selectedRegion || !file ? 0.5 : 1,
        }}
      >
        {uploadMutation.isPending ? 'Загружаем…' : 'Загрузить'}
      </button>
    </div>
  )
}

// ============================================================
// Строка региона
// ============================================================
interface RegionRowProps {
  info: CameraRegionInfo
  onDelete: () => void
  isDeleting: boolean
}

function RegionRow({ info, onDelete, isDeleting }: RegionRowProps) {
  return (
    <div
      className="p-2.5 rounded-lg flex items-center justify-between gap-2"
      style={{ backgroundColor: 'var(--tg-color-secondary-bg, #f1f1f1)' }}
    >
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium truncate">
          {info.reg_name ?? `Регион ${info.reg_code}`}
        </div>
        <div className="text-xs opacity-60 truncate">
          код {info.reg_code} · {formatSize(info.file_size_bytes)}
          {info.file_modified && (
            <> · {new Date(info.file_modified).toLocaleDateString('ru-RU')}</>
          )}
        </div>
      </div>
      <button
        type="button"
        onClick={() => {
          if (confirm(`Удалить файл камер для региона ${info.reg_code}?`)) {
            onDelete()
          }
        }}
        disabled={isDeleting}
        className="text-xs px-2.5 py-1.5 rounded-lg"
        style={{
          backgroundColor: 'rgba(255, 59, 48, 0.1)',
          color: 'var(--tg-color-destructive, #ff3b30)',
        }}
      >
        {isDeleting ? '…' : 'Удалить'}
      </button>
    </div>
  )
}
