import { defineStore } from 'pinia'
import { ref } from 'vue'

export const usePipelineStore = defineStore('pipeline', () => {
  /** Current playback / cursor time in ms (shared between video, waveform, subtitle list) */
  const currentTimeMs = ref(0)

  /** ASR pipeline progress 0.0 ~ 1.0 */
  const progress = ref(0)

  /** Whether the ASR pipeline is actively processing */
  const active = ref(false)

  /** Number of ASR chunks processed so far */
  const chunksProcessed = ref(0)

  /** Whether translation is in progress */
  const translating = ref(false)

  /** Translation progress 0.0 ~ 1.0 */
  const translationProgress = ref(0)

  /** Last translation error message (empty string = no error) */
  const translationError = ref('')

  /** Last translation warning message (empty string = none) */
  const translationWarning = ref('')

  function setProgress(p: number, chunks?: number) {
    progress.value = p
    if (chunks !== undefined) chunksProcessed.value = chunks
  }

  function setActive(v: boolean) {
    active.value = v
    if (!v) progress.value = 0
  }

  function setTranslating(v: boolean, p: number = 0) {
    translating.value = v
    translationProgress.value = p
    if (v) {
      // Clear stale error/warning when a new translation starts
      translationError.value = ''
      translationWarning.value = ''
    }
  }

  function setTranslationError(msg: string) {
    translationError.value = msg
  }

  function setTranslationWarning(msg: string) {
    translationWarning.value = msg
  }

  function seekTo(ms: number) {
    currentTimeMs.value = ms
  }

  /** Call on project enter to clear state from previous project */
  function reset() {
    active.value = false
    progress.value = 0
    chunksProcessed.value = 0
    translating.value = false
    translationProgress.value = 0
    translationError.value = ''
    translationWarning.value = ''
    // Note: intentionally keep currentTimeMs (it resets on video load)
  }

  return {
    currentTimeMs, progress, active, chunksProcessed,
    translating, translationProgress, translationError, translationWarning,
    setProgress, setActive, setTranslating, setTranslationError, setTranslationWarning,
    seekTo, reset,
  }
})
