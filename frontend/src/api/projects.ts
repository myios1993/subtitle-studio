import { api } from './client'
import type { Project, ProjectListItem } from './types'

export const projectsApi = {
  list: () => api.get<ProjectListItem[]>('/projects'),

  get: (id: number) => api.get<Project>(`/projects/${id}`),

  create: (data: { name: string; capture_mode: string; source_audio_path?: string; source_video_path?: string }) =>
    api.post<Project>('/projects', data),

  update: (id: number, data: Partial<Pick<Project, 'name' | 'status' | 'source_language'>>) =>
    api.patch<Project>(`/projects/${id}`, data),

  delete: (id: number) => api.delete(`/projects/${id}`),

  updateSpeaker: (projectId: number, speakerId: string, data: { label?: string; color?: string }) =>
    api.patch(`/projects/${projectId}/speakers/${speakerId}`, data),
}
