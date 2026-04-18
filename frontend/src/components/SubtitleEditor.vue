<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useProjectStore } from '../stores/project'
import { usePipelineStore } from '../stores/pipeline'
import { useHistoryStore } from '../stores/history'
import { segmentsApi } from '../api/segments'
import SegmentRow from './SegmentRow.vue'
import type { Segment } from '../api/types'

const store = useProjectStore()
const pipeline = usePipelineStore()
const historyStore = useHistoryStore()

// ---------------------------------------------------------------------------
// Display
// ---------------------------------------------------------------------------
const displaySegments = computed(() => store.filteredSegments)

function isActive(seg: Segment): boolean {
  return pipeline.currentTimeMs >= seg.start_ms && pipeline.currentTimeMs < seg.end_ms
}

function formatTime(ms: number): string {
  const totalS = Math.floor(ms / 1000)
  const m = Math.floor(totalS / 60)
  const s = totalS % 60
  const tenths = Math.floor((ms % 1000) / 100)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${tenths}`
}

// ---------------------------------------------------------------------------
// Selection model
// ---------------------------------------------------------------------------
const selectedIds = ref<Set<number>>(new Set())

function toggleSelect(id: number) {
  // Rebuild the Set so Vue's reactivity system detects the mutation
  const next = new Set(selectedIds.value)
  if (next.has(id)) {
    next.delete(id)
  } else {
    next.add(id)
  }
  selectedIds.value = next
}

function clearSelection() {
  selectedIds.value = new Set()
}

// ---------------------------------------------------------------------------
// Bulk delete selected segments
// ---------------------------------------------------------------------------
const bulkDeleting = ref(false)

async function onBulkDelete() {
  if (selectedIds.value.size === 0 || !store.project) return
  if (!confirm(`确定删除所选的 ${selectedIds.value.size} 条字幕？`)) return
  bulkDeleting.value = true
  const ids = [...selectedIds.value]
  try {
    await segmentsApi.bulkDelete(store.project.id, ids)
    store.removeSegmentsByIds(ids)
    clearSelection()
  } catch (e: any) {
    console.error('批量删除失败:', e.message)
  } finally {
    bulkDeleting.value = false
  }
}

// ---------------------------------------------------------------------------
// Merge
// ---------------------------------------------------------------------------
async function onMerge() {
  if (selectedIds.value.size < 2) return
  const ids = [...selectedIds.value]
  try {
    await store.mergeSegments(ids)
  } catch (e: any) {
    console.error('合并失败:', e.message)
  } finally {
    clearSelection()
  }
}

// ---------------------------------------------------------------------------
// Split dialog
// ---------------------------------------------------------------------------
const splittingSegment = ref<Segment | null>(null)
const splitAtMs = ref(0)
const splitError = ref<string | null>(null)

function onSplit(seg: Segment) {
  splittingSegment.value = seg
  splitError.value = null
  const mid = Math.round((seg.start_ms + seg.end_ms) / 2)
  const cur = pipeline.currentTimeMs
  splitAtMs.value = cur > seg.start_ms && cur < seg.end_ms ? cur : mid
}

function cancelSplit() {
  splittingSegment.value = null
  splitError.value = null
}

async function confirmSplit() {
  if (!splittingSegment.value) return
  splitError.value = null
  try {
    await store.splitSegment(splittingSegment.value.id, splitAtMs.value)
    splittingSegment.value = null
  } catch (e: any) {
    splitError.value = e.message ?? '拆分失败'
  }
}

// ---------------------------------------------------------------------------
// Segment click → seek
// ---------------------------------------------------------------------------
function onSegmentClick(seg: Segment) {
  pipeline.seekTo(seg.start_ms)
}

// ---------------------------------------------------------------------------
// Save / delete forwarded from SegmentRow (legacy path — row now calls store directly)
// ---------------------------------------------------------------------------
async function onDelete(id: number) {
  if (confirm('确定删除这条字幕？')) {
    await store.deleteSegment(id)
    // Also remove from selection if present
    if (selectedIds.value.has(id)) {
      const next = new Set(selectedIds.value)
      next.delete(id)
      selectedIds.value = next
    }
  }
}

// ---------------------------------------------------------------------------
// Global keyboard shortcuts
// ---------------------------------------------------------------------------
function onGlobalKey(e: KeyboardEvent) {
  // Ignore if user is typing in an input/textarea (except the shortcuts we explicitly want)
  const tag = (e.target as HTMLElement).tagName
  const inField = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'

  if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
    // Undo is allowed even inside text areas so the global undo fires
    if (!inField) {
      e.preventDefault()
      store.undo()
    }
  }
  if (
    (e.ctrlKey || e.metaKey) &&
    (e.key === 'y' || (e.key === 'z' && e.shiftKey))
  ) {
    if (!inField) {
      e.preventDefault()
      store.redo()
    }
  }
}

onMounted(() => {
  window.addEventListener('keydown', onGlobalKey)
})
onUnmounted(() => {
  window.removeEventListener('keydown', onGlobalKey)
})

// ---------------------------------------------------------------------------
// Auto-scroll active segment into view
// ---------------------------------------------------------------------------
watch(
  () => pipeline.currentTimeMs,
  () => {
    const active = store.filteredSegments.find(
      s => pipeline.currentTimeMs >= s.start_ms && pipeline.currentTimeMs < s.end_ms
    )
    if (active) {
      const el = document.getElementById(`seg-${active.id}`)
      el?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }
)
</script>

<template>
  <div class="flex flex-col h-full">

    <!-- ===== Toolbar ===== -->
    <div class="flex items-center gap-1 px-3 py-1.5 border-b border-gray-800 bg-gray-900/60">
      <!-- Undo -->
      <button
        @click="store.undo()"
        :disabled="!historyStore.canUndo"
        class="flex items-center gap-1 px-2 py-1 rounded text-xs transition-colors
               disabled:opacity-30 disabled:cursor-not-allowed
               text-gray-400 hover:text-white hover:bg-gray-700 disabled:hover:bg-transparent"
        title="撤销 (Ctrl+Z)"
      >
        <svg class="w-3.5 h-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8">
          <path d="M3 7 L3 3 M3 7 L7 7" stroke-linecap="round" stroke-linejoin="round"/>
          <path d="M3 7 Q3 13 10 13 Q14 13 14 9 Q14 5 10 5 L6 5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        撤销
      </button>

      <!-- Redo -->
      <button
        @click="store.redo()"
        :disabled="!historyStore.canRedo"
        class="flex items-center gap-1 px-2 py-1 rounded text-xs transition-colors
               disabled:opacity-30 disabled:cursor-not-allowed
               text-gray-400 hover:text-white hover:bg-gray-700 disabled:hover:bg-transparent"
        title="重做 (Ctrl+Y)"
      >
        <svg class="w-3.5 h-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8">
          <path d="M13 7 L13 3 M13 7 L9 7" stroke-linecap="round" stroke-linejoin="round"/>
          <path d="M13 7 Q13 13 6 13 Q2 13 2 9 Q2 5 6 5 L10 5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        重做
      </button>

      <!-- Separator -->
      <div class="w-px h-4 bg-gray-700 mx-1 shrink-0" />

      <!-- Bulk delete (visible when ≥1 selected) -->
      <button
        v-if="selectedIds.size >= 1"
        @click="onBulkDelete"
        :disabled="bulkDeleting"
        class="flex items-center gap-1 px-2 py-1 rounded text-xs transition-colors
               text-red-400 hover:text-white hover:bg-red-600/60 disabled:opacity-40"
        title="删除所选字幕"
      >
        <svg class="w-3.5 h-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8">
          <path d="M3 4 h10 M6 4 V2 h4 V4 M5 4 v9 h6 V4" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        删除 ({{ selectedIds.size }})
      </button>

      <!-- Merge (visible only when ≥2 selected) -->
      <button
        v-if="selectedIds.size >= 2"
        @click="onMerge"
        class="flex items-center gap-1 px-2 py-1 rounded text-xs transition-colors
               text-amber-400 hover:text-white hover:bg-amber-600/60"
        title="合并所选字幕"
      >
        <svg class="w-3.5 h-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8">
          <path d="M3 4 h10 M3 8 h6 M3 12 h10" stroke-linecap="round"/>
          <path d="M11 6 L14 8 L11 10" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        合并 ({{ selectedIds.size }})
      </button>

      <!-- Cancel selection -->
      <button
        v-if="selectedIds.size > 0"
        @click="clearSelection"
        class="flex items-center gap-1 px-2 py-1 rounded text-xs transition-colors
               text-gray-400 hover:text-white hover:bg-gray-700"
      >
        取消选择
      </button>

      <!-- Right spacer + meta info -->
      <div class="ml-auto flex items-center gap-3 text-xs text-gray-500 select-none">
        <span>{{ displaySegments.length }} 条字幕</span>
        <span v-if="pipeline.active" class="text-blue-400 animate-pulse flex items-center gap-1">
          <span class="inline-block w-1.5 h-1.5 rounded-full bg-blue-400 animate-ping" />
          识别中...
        </span>
      </div>
    </div>

    <!-- ===== Search bar ===== -->
    <div class="px-3 py-2 border-b border-gray-800">
      <input
        v-model="store.searchQuery"
        type="text"
        placeholder="搜索字幕内容..."
        class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white
               placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
      />
    </div>

    <!-- ===== Split dialog (inline, fixed below search bar) ===== -->
    <Transition
      enter-active-class="transition duration-150 ease-out"
      enter-from-class="opacity-0 -translate-y-1"
      enter-to-class="opacity-100 translate-y-0"
      leave-active-class="transition duration-100 ease-in"
      leave-from-class="opacity-100 translate-y-0"
      leave-to-class="opacity-0 -translate-y-1"
    >
      <div
        v-if="splittingSegment"
        class="mx-3 mb-2 mt-1 rounded-lg border border-yellow-600/40 bg-yellow-500/5 p-3 space-y-3"
      >
        <div class="flex items-center justify-between">
          <span class="text-xs font-semibold text-yellow-400">拆分字幕</span>
          <button
            @click="cancelSplit"
            class="text-gray-500 hover:text-gray-200 transition-colors"
            title="取消"
          >
            <svg class="w-3.5 h-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M3 3 L13 13 M13 3 L3 13" stroke-linecap="round"/>
            </svg>
          </button>
        </div>

        <!-- Current split position display -->
        <div class="text-xs text-gray-300 font-mono tabular-nums">
          拆分位置：<span class="text-yellow-300">{{ formatTime(splitAtMs) }}</span>
          <span class="text-gray-600 ml-2">
            ({{ formatTime(splittingSegment.start_ms) }} – {{ formatTime(splittingSegment.end_ms) }})
          </span>
        </div>

        <!-- Range slider -->
        <input
          type="range"
          :min="splittingSegment.start_ms + 50"
          :max="splittingSegment.end_ms - 50"
          v-model.number="splitAtMs"
          step="50"
          class="w-full h-1.5 rounded-full appearance-none cursor-pointer
                 bg-gray-700 accent-yellow-400"
        />

        <!-- Error message -->
        <p v-if="splitError" class="text-xs text-red-400">{{ splitError }}</p>

        <!-- Actions -->
        <div class="flex gap-2 justify-end">
          <button
            @click="cancelSplit"
            class="px-3 py-1 rounded text-xs text-gray-400 hover:text-white hover:bg-gray-700 transition-colors"
          >
            取消
          </button>
          <button
            @click="confirmSplit"
            class="px-3 py-1 rounded text-xs bg-yellow-500 hover:bg-yellow-400 text-black font-medium transition-colors"
          >
            确认拆分
          </button>
        </div>
      </div>
    </Transition>

    <!-- ===== Segment list ===== -->
    <div class="flex-1 overflow-y-auto">
      <div
        v-if="displaySegments.length === 0"
        class="text-center py-20 text-gray-600 text-sm"
      >
        {{ store.searchQuery ? '无匹配结果' : '暂无字幕，启动处理后将实时显示' }}
      </div>

      <SegmentRow
        v-for="seg in displaySegments"
        :key="seg.id"
        :segment="seg"
        :speaker="store.speakerMap[seg.speaker_id ?? '']"
        :is-active="isActive(seg)"
        :selected="selectedIds.has(seg.id)"
        @delete="onDelete"
        @click="onSegmentClick"
        @toggle-select="toggleSelect(seg.id)"
        @split="onSplit(seg)"
      />
    </div>
  </div>
</template>
