import { api } from './client'
import type { AudioDevices } from './types'

export const pipelineApi = {
  start: (projectId: number, opts?: { mode?: string; device_index?: number; file_path?: string; language?: string; num_speakers?: number; resume?: boolean }) =>
    api.post<{ message: string; project_id: number; mode: string }>(`/pipeline/${projectId}/start`, opts || {}),

  /** Resume from last saved segment (file mode only). */
  resume: (projectId: number, opts?: { language?: string; num_speakers?: number }) =>
    api.post<{ message: string; project_id: number; mode: string }>(`/pipeline/${projectId}/start`, { ...(opts || {}), resume: true }),

  /** Translate segments; source_lang overrides per-segment auto-detection for this run. */
  translateWithLang: (projectId: number, sourceLang?: string) =>
    api.post<{ message: string }>(`/pipeline/${projectId}/translate`, { source_lang: sourceLang }),

  stop: (projectId: number) =>
    api.post<{ message: string }>(`/pipeline/${projectId}/stop`),

  /** Translate all untranslated segments (returns immediately; progress via WebSocket). */
  translate: (projectId: number) =>
    api.post<{ message: string }>(`/pipeline/${projectId}/translate`),

  /** Clear all segments + reset project status to 'idle'. */
  reset: (projectId: number) =>
    api.post<{ message: string }>(`/pipeline/${projectId}/reset`),

  status: (projectId: number) =>
    api.get<{ project_id: number; status: string; pipeline_active: boolean }>(`/pipeline/${projectId}/status`),

  getAudioDevices: () =>
    api.get<AudioDevices>('/audio/devices'),
}
