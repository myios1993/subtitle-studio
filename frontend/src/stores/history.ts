// Pinia store for undo/redo of segment edits
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

interface EditCommand {
  segmentId: number
  field: string   // 'original_text' | 'translated_text' | 'start_ms' | 'end_ms'
  oldValue: string | number | null
  newValue: string | number | null
  label?: string  // human-readable description, e.g. "编辑原文"
}

export const useHistoryStore = defineStore('history', () => {
  const undoStack = ref<EditCommand[]>([])
  const redoStack = ref<EditCommand[]>([])

  const canUndo = computed(() => undoStack.value.length > 0)
  const canRedo = computed(() => redoStack.value.length > 0)

  function push(cmd: EditCommand) {
    undoStack.value.push(cmd)
    redoStack.value = []   // new action clears redo
  }

  function popUndo(): EditCommand | undefined {
    return undoStack.value.pop()
  }

  function pushRedo(cmd: EditCommand) {
    redoStack.value.push(cmd)
  }

  function popRedo(): EditCommand | undefined {
    return redoStack.value.pop()
  }

  function clear() {
    undoStack.value = []
    redoStack.value = []
  }

  return { undoStack, redoStack, canUndo, canRedo, push, popUndo, pushRedo, popRedo, clear }
})
