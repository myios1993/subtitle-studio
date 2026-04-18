<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted, computed } from 'vue'
import { usePipelineStore } from '../stores/pipeline'

// ---------------------------------------------------------------------------
// Props / Emits / Expose
// ---------------------------------------------------------------------------
const props = defineProps<{
  projectId: number
}>()

const emit = defineEmits<{
  (e: 'timeupdate', ms: number): void
}>()

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------
const pipeline = usePipelineStore()

// ---------------------------------------------------------------------------
// Refs
// ---------------------------------------------------------------------------
const videoEl = ref<HTMLVideoElement | null>(null)
const isPlaying = ref(false)
const currentMs = ref(0)
const durationMs = ref(0)
const loadState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const errorMsg = ref('')
const volume = ref(1)
const isMuted = ref(false)

const showVolumeSlider = ref(false)

// rAF handle
let rafId: number | null = null
// Flag so we don't trigger our own watch-based seek
let internalUpdate = false

// ---------------------------------------------------------------------------
// Video source URL
// ---------------------------------------------------------------------------
const videoSrc = computed(() => `/api/audio/video/${props.projectId}`)

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function formatMs(ms: number): string {
  const totalSec = Math.floor(ms / 1000)
  const h = Math.floor(totalSec / 3600)
  const m = Math.floor((totalSec % 3600) / 60)
  const s = totalSec % 60
  if (h > 0) {
    return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  }
  return `${m}:${String(s).padStart(2, '0')}`
}

// ---------------------------------------------------------------------------
// rAF loop — updates currentTimeMs from video while playing
// ---------------------------------------------------------------------------
function startRaf() {
  if (rafId !== null) return
  function tick() {
    const el = videoEl.value
    if (el && !el.paused) {
      const ms = Math.round(el.currentTime * 1000)
      currentMs.value = ms
      internalUpdate = true
      pipeline.seekTo(ms)
      // Reset flag after microtask so watcher doesn't fight us
      Promise.resolve().then(() => { internalUpdate = false })
      emit('timeupdate', ms)
    }
    rafId = requestAnimationFrame(tick)
  }
  rafId = requestAnimationFrame(tick)
}

function stopRaf() {
  if (rafId !== null) {
    cancelAnimationFrame(rafId)
    rafId = null
  }
}

// ---------------------------------------------------------------------------
// Video element event handlers
// ---------------------------------------------------------------------------
function onLoadStart() {
  loadState.value = 'loading'
  errorMsg.value = ''
}

function onCanPlay() {
  loadState.value = 'ready'
  const el = videoEl.value
  if (el) {
    durationMs.value = Math.round(el.duration * 1000)
  }
}

function onDurationChange() {
  const el = videoEl.value
  if (el && isFinite(el.duration)) {
    durationMs.value = Math.round(el.duration * 1000)
  }
}

function onPlay() {
  isPlaying.value = true
  startRaf()
}

function onPause() {
  isPlaying.value = false
  stopRaf()
  // One final sync
  const el = videoEl.value
  if (el) {
    const ms = Math.round(el.currentTime * 1000)
    currentMs.value = ms
    internalUpdate = true
    pipeline.seekTo(ms)
    emit('timeupdate', ms)
    Promise.resolve().then(() => { internalUpdate = false })
  }
}

function onEnded() {
  isPlaying.value = false
  stopRaf()
}

function onError() {
  loadState.value = 'error'
  const el = videoEl.value
  const code = el?.error?.code ?? 0
  const msgs: Record<number, string> = {
    1: '视频加载已中止',
    2: '网络错误',
    3: '视频解码失败',
    4: '视频格式不支持',
  }
  errorMsg.value = msgs[code] ?? '视频加载失败'
}

// ---------------------------------------------------------------------------
// Controls
// ---------------------------------------------------------------------------
function togglePlay() {
  const el = videoEl.value
  if (!el || loadState.value === 'error') return
  if (el.paused) {
    el.play().catch(() => {})
  } else {
    el.pause()
  }
}

function onProgressClick(e: MouseEvent) {
  const bar = e.currentTarget as HTMLElement
  const rect = bar.getBoundingClientRect()
  const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
  seekToRatio(ratio)
}

function seekToRatio(ratio: number) {
  const el = videoEl.value
  if (!el || !isFinite(el.duration)) return
  const targetMs = Math.round(ratio * durationMs.value)
  el.currentTime = targetMs / 1000
  currentMs.value = targetMs
  internalUpdate = true
  pipeline.seekTo(targetMs)
  emit('timeupdate', targetMs)
  Promise.resolve().then(() => { internalUpdate = false })
}

// Expose seekTo for parent usage
function seekTo(ms: number) {
  const el = videoEl.value
  if (!el) return
  el.currentTime = ms / 1000
  currentMs.value = ms
}

defineExpose({ seekTo })

function toggleMute() {
  const el = videoEl.value
  if (!el) return
  isMuted.value = !isMuted.value
  el.muted = isMuted.value
}

function onVolumeChange(e: Event) {
  const input = e.target as HTMLInputElement
  const val = Number(input.value)
  volume.value = val
  const el = videoEl.value
  if (el) {
    el.volume = val
    isMuted.value = val === 0
  }
}

// ---------------------------------------------------------------------------
// Watch pipeline.currentTimeMs — seek video when changed externally
// (e.g. clicking a subtitle row)
// ---------------------------------------------------------------------------
watch(
  () => pipeline.currentTimeMs,
  (ms) => {
    if (internalUpdate) return
    const el = videoEl.value
    if (!el) return
    // Only seek if meaningfully different (>50ms) to avoid feedback loops
    const diff = Math.abs(el.currentTime * 1000 - ms)
    if (diff > 50) {
      el.currentTime = ms / 1000
      currentMs.value = ms
    }
  }
)

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------
onMounted(() => {
  const el = videoEl.value
  if (el) {
    el.volume = volume.value
    loadState.value = 'loading'
  }
})

onUnmounted(() => {
  stopRaf()
})

// ---------------------------------------------------------------------------
// Progress bar percent
// ---------------------------------------------------------------------------
const progressPct = computed(() =>
  durationMs.value > 0 ? (currentMs.value / durationMs.value) * 100 : 0
)
</script>

<template>
  <div class="flex flex-col bg-gray-900 rounded-xl overflow-hidden w-full select-none">

    <!-- Video element -->
    <div class="relative bg-black w-full" style="aspect-ratio: 16/9;">
      <video
        ref="videoEl"
        :src="videoSrc"
        class="w-full h-full object-contain"
        preload="metadata"
        @loadstart="onLoadStart"
        @canplay="onCanPlay"
        @durationchange="onDurationChange"
        @play="onPlay"
        @pause="onPause"
        @ended="onEnded"
        @error="onError"
        @click="togglePlay"
      />

      <!-- Loading overlay -->
      <div
        v-if="loadState === 'loading'"
        class="absolute inset-0 flex items-center justify-center bg-black/60"
      >
        <div class="flex flex-col items-center gap-3 text-gray-400">
          <svg class="w-8 h-8 animate-spin" viewBox="0 0 24 24" fill="none">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 100 16v-4l-3 3 3 3v-4a8 8 0 01-8-8z"/>
          </svg>
          <span class="text-sm">加载视频中...</span>
        </div>
      </div>

      <!-- Error overlay -->
      <div
        v-if="loadState === 'error'"
        class="absolute inset-0 flex items-center justify-center bg-black/80"
      >
        <div class="flex flex-col items-center gap-2 text-center px-6">
          <svg class="w-10 h-10 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"/>
          </svg>
          <p class="text-red-400 text-sm font-medium">{{ errorMsg }}</p>
          <p class="text-gray-500 text-xs">项目 ID: {{ projectId }}</p>
        </div>
      </div>

      <!-- Play/pause center overlay on pause (only when ready) -->
      <Transition name="fade">
        <div
          v-if="loadState === 'ready' && !isPlaying"
          class="absolute inset-0 flex items-center justify-center pointer-events-none"
        >
          <div class="w-14 h-14 rounded-full bg-black/50 flex items-center justify-center">
            <svg class="w-7 h-7 text-white translate-x-0.5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M8 5v14l11-7L8 5z"/>
            </svg>
          </div>
        </div>
      </Transition>
    </div>

    <!-- Controls bar -->
    <div class="px-3 pt-2 pb-3 bg-gray-850 space-y-2" style="background: #1a1f2e;">

      <!-- Progress bar -->
      <div
        class="relative h-1.5 bg-gray-700 rounded-full cursor-pointer group"
        @click="onProgressClick"
      >
        <!-- Buffered/played -->
        <div
          class="absolute inset-y-0 left-0 bg-blue-500 rounded-full transition-none"
          :style="{ width: `${progressPct}%` }"
        />
        <!-- Thumb -->
        <div
          class="absolute top-1/2 -translate-y-1/2 w-3 h-3 bg-white rounded-full shadow opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none"
          :style="{ left: `calc(${progressPct}% - 6px)` }"
        />
      </div>

      <!-- Buttons row -->
      <div class="flex items-center gap-2">

        <!-- Play/Pause -->
        <button
          @click="togglePlay"
          :disabled="loadState !== 'ready'"
          class="w-8 h-8 flex items-center justify-center rounded-lg text-white hover:bg-gray-700 disabled:opacity-30 disabled:cursor-not-allowed transition-colors flex-shrink-0"
          :title="isPlaying ? '暂停' : '播放'"
        >
          <!-- Pause icon -->
          <svg v-if="isPlaying" class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
            <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>
          </svg>
          <!-- Play icon -->
          <svg v-else class="w-4 h-4 translate-x-px" fill="currentColor" viewBox="0 0 24 24">
            <path d="M8 5v14l11-7L8 5z"/>
          </svg>
        </button>

        <!-- Time display -->
        <span class="text-xs text-gray-400 tabular-nums flex-shrink-0">
          {{ formatMs(currentMs) }} / {{ formatMs(durationMs) }}
        </span>

        <div class="flex-1" />

        <!-- Volume control -->
        <div class="relative flex items-center gap-1.5" @mouseenter="showVolumeSlider = true" @mouseleave="showVolumeSlider = false">
          <button
            @click="toggleMute"
            class="w-8 h-8 flex items-center justify-center rounded-lg text-gray-400 hover:text-white hover:bg-gray-700 transition-colors flex-shrink-0"
            :title="isMuted ? '取消静音' : '静音'"
          >
            <!-- Muted icon -->
            <svg v-if="isMuted || volume === 0" class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
              <path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"/>
            </svg>
            <!-- Volume icon -->
            <svg v-else class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
              <path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02z"/>
            </svg>
          </button>

          <Transition name="slide-fade">
            <input
              v-if="showVolumeSlider"
              type="range"
              min="0"
              max="1"
              step="0.05"
              :value="isMuted ? 0 : volume"
              @input="onVolumeChange"
              class="w-20 h-1 accent-blue-500 cursor-pointer"
            />
          </Transition>
        </div>

      </div>
    </div>
  </div>
</template>

<style scoped>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.slide-fade-enter-active,
.slide-fade-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}
.slide-fade-enter-from,
.slide-fade-leave-to {
  opacity: 0;
  transform: translateX(-4px);
}

/* Range input cross-browser baseline */
input[type='range'] {
  -webkit-appearance: none;
  appearance: none;
  background: transparent;
}
input[type='range']::-webkit-slider-runnable-track {
  height: 4px;
  background: #374151;
  border-radius: 9999px;
}
input[type='range']::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 12px;
  height: 12px;
  background: #3b82f6;
  border-radius: 50%;
  margin-top: -4px;
}
</style>
