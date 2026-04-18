import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { Project, Segment, Speaker } from '../api/types'
import { projectsApi } from '../api/projects'
import { segmentsApi } from '../api/segments'
import { useHistoryStore } from './history'

export const useProjectStore = defineStore('project', () => {
  // --- State ---
  const project = ref<Project | null>(null)
  const segments = ref<Segment[]>([])
  const loading = ref(false)
  const searchQuery = ref('')

  // --- Getters ---
  const speakerMap = computed(() => {
    const map: Record<string, Speaker> = {}
    if (project.value) {
      for (const s of project.value.speakers) {
        map[s.speaker_id] = s
      }
    }
    return map
  })

  const filteredSegments = computed(() => {
    if (!searchQuery.value) return segments.value
    const q = searchQuery.value.toLowerCase()
    return segments.value.filter(
      s => s.original_text.toLowerCase().includes(q) ||
           (s.translated_text && s.translated_text.toLowerCase().includes(q))
    )
  })

  // --- Actions ---
  async function loadProject(id: number) {
    loading.value = true
    try {
      project.value = await projectsApi.get(id)
      await loadSegments()
    } finally {
      loading.value = false
    }
  }

  async function loadSegments() {
    if (!project.value) return
    segments.value = await segmentsApi.list(project.value.id, { limit: 1000 })
  }

  /** Called by WebSocket when a new segment is created by the pipeline */
  function addSegment(seg: Segment) {
    // Insert in order by start_ms
    const idx = segments.value.findIndex(s => s.start_ms > seg.start_ms)
    if (idx === -1) {
      segments.value.push(seg)
    } else {
      segments.value.splice(idx, 0, seg)
    }
  }

  /** Called after a full save (returns complete segment from API) */
  function updateSegment(seg: Segment) {
    const idx = segments.value.findIndex(s => s.id === seg.id)
    if (idx !== -1) {
      segments.value[idx] = seg
    }
  }

  /**
   * Called by WebSocket when only some fields change (e.g. speaker backfill,
   * translation).  Merges the partial object into the existing segment so
   * unchanged fields are preserved.
   */
  function patchSegment(partial: Partial<Segment> & { id: number }) {
    const idx = segments.value.findIndex(s => s.id === partial.id)
    if (idx !== -1) {
      segments.value[idx] = { ...segments.value[idx], ...partial }
    }
  }

  async function saveSegment(segId: number, data: Partial<Segment>) {
    if (!project.value) return
    const updated = await segmentsApi.update(project.value.id, segId, data)
    updateSegment(updated)
  }

  async function deleteSegment(segId: number) {
    if (!project.value) return
    await segmentsApi.delete(project.value.id, segId)
    segments.value = segments.value.filter(s => s.id !== segId)
  }

  /** Remove segments by id list from the local state (called after bulk delete API). */
  function removeSegmentsByIds(ids: number[]) {
    const set = new Set(ids)
    segments.value = segments.value.filter(s => !set.has(s.id))
  }

  function updateProjectStatus(status: Project['status']) {
    if (project.value) {
      project.value.status = status
    }
  }

  /** Called by SegmentRow after debounce — records undo history and saves */
  async function saveSegmentWithHistory(
    segId: number,
    field: string,
    oldValue: string | number | null,
    newValue: string | number | null
  ) {
    const historyStore = useHistoryStore()
    historyStore.push({ segmentId: segId, field, oldValue, newValue })
    await saveSegment(segId, { [field]: newValue })
  }

  async function undo() {
    const historyStore = useHistoryStore()
    const cmd = historyStore.popUndo()
    if (!cmd || !project.value) return
    const updated = await segmentsApi.update(project.value.id, cmd.segmentId, { [cmd.field]: cmd.oldValue })
    updateSegment(updated)
    historyStore.pushRedo(cmd)
  }

  async function redo() {
    const historyStore = useHistoryStore()
    const cmd = historyStore.popRedo()
    if (!cmd || !project.value) return
    const updated = await segmentsApi.update(project.value.id, cmd.segmentId, { [cmd.field]: cmd.newValue })
    updateSegment(updated)
    historyStore.push(cmd)
  }

  async function mergeSegments(ids: number[]) {
    if (!project.value) return
    const merged = await segmentsApi.merge(project.value.id, ids)
    // Remove all merged segments and replace with the merged one
    segments.value = segments.value.filter(s => !ids.includes(s.id) || s.id === merged.id)
    const idx = segments.value.findIndex(s => s.id === merged.id)
    if (idx !== -1) {
      segments.value[idx] = merged
    } else {
      segments.value.push(merged)
      segments.value.sort((a, b) => a.start_ms - b.start_ms)
    }
  }

  async function splitSegment(id: number, splitAtMs: number) {
    if (!project.value) return
    const [a, b] = await segmentsApi.split(project.value.id, id, splitAtMs)
    // Replace original segment with the two resulting halves
    const idx = segments.value.findIndex(s => s.id === id)
    if (idx !== -1) {
      segments.value.splice(idx, 1, a, b)
    } else {
      segments.value.push(a, b)
      segments.value.sort((a, b) => a.start_ms - b.start_ms)
    }
  }

  function $reset() {
    project.value = null
    segments.value = []
    loading.value = false
    searchQuery.value = ''
  }

  return {
    project, segments, loading, searchQuery,
    speakerMap, filteredSegments,
    loadProject, loadSegments, addSegment, updateSegment, patchSegment,
    saveSegment, deleteSegment, removeSegmentsByIds, updateProjectStatus,
    saveSegmentWithHistory, undo, redo, mergeSegments, splitSegment,
    $reset,
  }
})
