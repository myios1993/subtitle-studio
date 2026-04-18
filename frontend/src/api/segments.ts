import { api } from './client'
import type { Segment } from './types'

export const segmentsApi = {
  list: (projectId: number, params?: { offset?: number; limit?: number; search?: string }) => {
    const query = new URLSearchParams()
    if (params?.offset) query.set('offset', String(params.offset))
    if (params?.limit) query.set('limit', String(params.limit))
    if (params?.search) query.set('search', params.search)
    const qs = query.toString()
    return api.get<Segment[]>(`/projects/${projectId}/segments${qs ? '?' + qs : ''}`)
  },

  update: (projectId: number, segmentId: number, data: Partial<Segment>) =>
    api.patch<Segment>(`/projects/${projectId}/segments/${segmentId}`, data),

  delete: (projectId: number, segmentId: number) =>
    api.delete(`/projects/${projectId}/segments/${segmentId}`),

  merge: (projectId: number, segmentIds: number[]) =>
    api.post<Segment>(`/projects/${projectId}/segments/merge`, segmentIds),

  split: (projectId: number, segmentId: number, splitAtMs: number) =>
    api.post<Segment[]>(`/projects/${projectId}/segments/${segmentId}/split`, { split_at_ms: splitAtMs }),

  /** Delete multiple segments by id. */
  bulkDelete: (projectId: number, ids: number[]) =>
    api.post<{ deleted: number }>(`/projects/${projectId}/segments/bulk-delete`, ids),
}
