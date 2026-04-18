<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useProjectStore } from '../stores/project'
import type { Segment, Speaker } from '../api/types'

// ---------------------------------------------------------------------------
// Props & emits
// ---------------------------------------------------------------------------
const props = defineProps<{
  segment: Segment
  speaker?: Speaker
  isActive: boolean
  selected: boolean
}>()

const emit = defineEmits<{
  save: [id: number, data: Partial<Segment>]
  delete: [id: number]
  click: [segment: Segment]
  'toggle-select': []
  split: []
}>()

// Call store directly for history-aware saves
const store = useProjectStore()

// ---------------------------------------------------------------------------
// Editable state — kept in sync when props change (e.g. undo/WS updates)
// ---------------------------------------------------------------------------
const editingText = ref(props.segment.original_text)
const editingTranslation = ref(props.segment.translated_text ?? '')

// Track "last committed" values so we can pass old/new to history
const lastSavedText = ref(props.segment.original_text)
const lastSavedTranslation = ref(props.segment.translated_text ?? '')

watch(
  () => props.segment.original_text,
  v => {
    editingText.value = v
    lastSavedText.value = v
  }
)
watch(
  () => props.segment.translated_text,
  v => {
    editingTranslation.value = v ?? ''
    lastSavedTranslation.value = v ?? ''
  }
)

// ---------------------------------------------------------------------------
// Undo-aware debounced save
// ---------------------------------------------------------------------------
let saveTimer: ReturnType<typeof setTimeout> | null = null

function debouncedSave(field: 'original_text' | 'translated_text', newValue: string) {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(async () => {
    const oldValue =
      field === 'original_text' ? lastSavedText.value : lastSavedTranslation.value

    // Only save if value actually changed
    if (oldValue === newValue) return

    await store.saveSegmentWithHistory(props.segment.id, field, oldValue, newValue)

    if (field === 'original_text') {
      lastSavedText.value = newValue
    } else {
      lastSavedTranslation.value = newValue
    }
  }, 500)
}

// ---------------------------------------------------------------------------
// Keyboard shortcuts inside textareas
// ---------------------------------------------------------------------------
function onTextareaKeydown(
  e: KeyboardEvent,
  field: 'original_text' | 'translated_text'
) {
  // Ctrl/Cmd+Enter → commit immediately (clear timer, blur)
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault()
    if (saveTimer) {
      clearTimeout(saveTimer)
      saveTimer = null
    }
    const currentVal = field === 'original_text' ? editingText.value : editingTranslation.value
    const oldVal = field === 'original_text' ? lastSavedText.value : lastSavedTranslation.value
    if (currentVal !== oldVal) {
      store.saveSegmentWithHistory(props.segment.id, field, oldVal, currentVal).then(() => {
        if (field === 'original_text') lastSavedText.value = currentVal
        else lastSavedTranslation.value = currentVal
      })
    }
    ;(e.target as HTMLElement).blur()
    return
  }

  // Escape → revert to last committed value and blur
  if (e.key === 'Escape') {
    e.preventDefault()
    if (saveTimer) {
      clearTimeout(saveTimer)
      saveTimer = null
    }
    if (field === 'original_text') {
      editingText.value = lastSavedText.value
    } else {
      editingTranslation.value = lastSavedTranslation.value
    }
    ;(e.target as HTMLElement).blur()
    return
  }

  // Stop Ctrl+Z / Ctrl+Y from bubbling to the global handler while in textarea
  if ((e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'y')) {
    e.stopPropagation()
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function formatTime(ms: number): string {
  const totalS = Math.floor(ms / 1000)
  const m = Math.floor(totalS / 60)
  const h = Math.floor(m / 60)
  const mm = String(m % 60).padStart(2, '0')
  const ss = String(totalS % 60).padStart(2, '0')
  const tenths = Math.floor((ms % 1000) / 100)
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}.${tenths}`
}

const isLowConfidence = computed(
  () => props.segment.confidence != null && props.segment.confidence < 0.7
)
</script>

<template>
  <div
    :id="`seg-${segment.id}`"
    @click="emit('click', segment)"
    :class="[
      'group flex gap-2 px-3 py-2 border-l-2 cursor-pointer transition-colors',
      isActive
        ? 'border-blue-500 bg-blue-500/10'
        : selected
          ? 'border-indigo-500 bg-indigo-500/5'
          : 'border-transparent hover:bg-gray-800/50',
      isLowConfidence ? 'bg-red-500/5' : '',
    ]"
  >
    <!-- ===== Checkbox ===== -->
    <div class="shrink-0 flex items-start pt-1.5">
      <input
        type="checkbox"
        :checked="selected"
        @change.stop="emit('toggle-select')"
        @click.stop
        class="w-3.5 h-3.5 rounded border border-gray-600 bg-gray-800 cursor-pointer
               accent-indigo-500 transition-opacity
               opacity-0 group-hover:opacity-100"
        :class="{ 'opacity-100': selected }"
        title="选择此字幕"
      />
    </div>

    <!-- ===== Timestamp ===== -->
    <div class="shrink-0 w-20 text-xs text-gray-500 pt-1 font-mono tabular-nums leading-snug">
      <div>{{ formatTime(segment.start_ms) }}</div>
      <div class="text-gray-700">{{ formatTime(segment.end_ms) }}</div>
    </div>

    <!-- ===== Speaker badge ===== -->
    <div class="shrink-0 w-16 pt-1">
      <span
        v-if="segment.speaker_id && speaker"
        class="text-xs px-1.5 py-0.5 rounded-full truncate block text-center leading-snug"
        :style="{
          backgroundColor: (speaker.color ?? '#6b7280') + '22',
          color: speaker.color ?? '#6b7280',
          border: `1px solid ${speaker.color ?? '#6b7280'}44`,
        }"
      >
        {{ speaker.label ?? segment.speaker_id }}
      </span>
      <span v-else class="text-xs text-gray-700 block text-center">?</span>
    </div>

    <!-- ===== Text content ===== -->
    <div class="flex-1 min-w-0 space-y-1">
      <!-- Original text -->
      <textarea
        v-model="editingText"
        rows="1"
        @input="debouncedSave('original_text', editingText)"
        @keydown="onTextareaKeydown($event, 'original_text')"
        @click.stop
        class="w-full bg-transparent text-sm text-gray-200 resize-none outline-none
               border-none p-0 leading-relaxed focus:text-white transition-colors"
        :class="{ 'text-red-300': isLowConfidence }"
        placeholder="原文..."
      />
      <!-- Translation -->
      <textarea
        v-model="editingTranslation"
        rows="1"
        @input="debouncedSave('translated_text', editingTranslation)"
        @keydown="onTextareaKeydown($event, 'translated_text')"
        @click.stop
        class="w-full bg-transparent text-sm text-gray-500 resize-none outline-none
               border-none p-0 leading-relaxed focus:text-gray-300 transition-colors"
        placeholder="翻译..."
      />
    </div>

    <!-- ===== Confidence & actions ===== -->
    <div class="shrink-0 flex flex-col items-end gap-1 pt-1">
      <!-- Confidence badge -->
      <span
        v-if="segment.confidence != null"
        class="text-xs tabular-nums"
        :class="isLowConfidence ? 'text-red-400' : 'text-gray-600'"
        :title="`置信度 ${Math.round(segment.confidence * 100)}%`"
      >
        {{ Math.round(segment.confidence * 100) }}%
      </span>

      <!-- Edited indicator -->
      <span
        v-if="segment.is_manually_edited"
        class="text-xs text-blue-500 opacity-0 group-hover:opacity-100 transition-opacity"
        title="已手动编辑"
      >
        ✎
      </span>

      <!-- Split button -->
      <button
        @click.stop="emit('split')"
        class="text-gray-700 hover:text-yellow-400 text-xs opacity-0
               group-hover:opacity-100 transition-opacity leading-none"
        title="拆分字幕"
      >
        拆分
      </button>

      <!-- Delete button -->
      <button
        @click.stop="emit('delete', segment.id)"
        class="text-gray-700 hover:text-red-400 text-xs opacity-0
               group-hover:opacity-100 transition-opacity leading-none"
        title="删除字幕"
      >
        删除
      </button>
    </div>
  </div>
</template>
