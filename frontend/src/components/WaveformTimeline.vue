<template>
  <div class="waveform-timeline bg-gray-900 select-none" :style="{ height: TOTAL_HEIGHT + 'px' }">
    <!-- Loading overlay -->
    <div
      v-if="isLoading"
      class="absolute inset-0 flex items-center justify-center z-10 bg-gray-900 bg-opacity-80"
    >
      <svg
        class="animate-spin h-6 w-6 text-blue-400"
        xmlns="http://www.w3.org/2000/svg"
        fill="none"
        viewBox="0 0 24 24"
      >
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
        <path
          class="opacity-75"
          fill="currentColor"
          d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
        />
      </svg>
      <span class="ml-2 text-sm text-gray-400">加载波形中…</span>
    </div>

    <!-- Error / no-audio placeholder -->
    <div
      v-if="hasError && !isLoading"
      class="absolute inset-0 flex items-center justify-center z-10"
    >
      <span class="text-sm text-gray-500">暂无音频波形</span>
    </div>

    <!-- WaveSurfer container -->
    <div
      ref="waveformEl"
      class="w-full"
      :style="{ height: WAVE_HEIGHT + 'px', opacity: hasError ? 0 : 1 }"
    />

    <!-- Controls bar -->
    <div
      class="flex items-center gap-3 px-3 bg-gray-800 border-t border-gray-700"
      :style="{ height: CONTROLS_HEIGHT + 'px' }"
    >
      <!-- Play / Pause button -->
      <button
        class="flex-shrink-0 w-7 h-7 flex items-center justify-center rounded text-gray-300 hover:text-white hover:bg-gray-700 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        :disabled="!isReady || hasError"
        :title="playing ? '暂停' : '播放'"
        @click="togglePlayPause"
      >
        <!-- Play icon -->
        <svg v-if="!playing" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4">
          <path d="M6.3 2.84A1.5 1.5 0 004 4.11v11.78a1.5 1.5 0 002.3 1.27l9.344-5.891a1.5 1.5 0 000-2.538L6.3 2.84z" />
        </svg>
        <!-- Pause icon -->
        <svg v-else xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4">
          <path d="M5.75 3a.75.75 0 00-.75.75v12.5c0 .414.336.75.75.75h1.5a.75.75 0 00.75-.75V3.75A.75.75 0 007.25 3h-1.5zM12.75 3a.75.75 0 00-.75.75v12.5c0 .414.336.75.75.75h1.5a.75.75 0 00.75-.75V3.75a.75.75 0 00-.75-.75h-1.5z" />
        </svg>
      </button>

      <!-- Time display -->
      <span class="text-xs tabular-nums text-gray-400 font-mono">
        {{ formatTime(currentTimeSec) }}
        <span class="text-gray-600 mx-0.5">/</span>
        {{ formatTime(duration) }}
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import WaveSurfer from 'wavesurfer.js'
import RegionsPlugin from 'wavesurfer.js/dist/plugins/regions.js'
import { usePipelineStore } from '../stores/pipeline'
import { useProjectStore } from '../stores/project'

// ── Layout constants ───────────────────────────────────────────────────────
const WAVE_HEIGHT = 80
const CONTROLS_HEIGHT = 28
const TOTAL_HEIGHT = WAVE_HEIGHT + CONTROLS_HEIGHT

// ── Props ──────────────────────────────────────────────────────────────────
const props = defineProps<{
  projectId: number
}>()

// ── Stores ─────────────────────────────────────────────────────────────────
const pipeline = usePipelineStore()
const projectStore = useProjectStore()

// ── Template refs ──────────────────────────────────────────────────────────
const waveformEl = ref<HTMLDivElement | null>(null)

// ── Component state ────────────────────────────────────────────────────────
const isLoading = ref(true)
const hasError = ref(false)
const isReady = ref(false)
const playing = ref(false)
const duration = ref(0)
const currentTimeSec = ref(0)

/** Prevent feedback loop: when we programmatically seek wavesurfer we
 *  temporarily block the timeupdate handler from calling pipeline.seekTo. */
let isSyncing = false

/** True while the user is actively dragging/resizing a region — suppresses
 *  the external currentTimeMs watcher so it doesn't fight the drag. */
const isDragging = ref(false)

// ── WaveSurfer instances ───────────────────────────────────────────────────
let ws: WaveSurfer | null = null
let regions: ReturnType<typeof RegionsPlugin.create> | null = null

/** Maps region.id (= String(seg.id)) → segment numeric id */
const regionSegMap = new Map<string, number>()

// ── Helpers ────────────────────────────────────────────────────────────────
function formatTime(sec: number): string {
  if (!isFinite(sec) || isNaN(sec)) return '0:00'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

function speakerColor(speakerId: string | null): string {
  if (!speakerId) return '#6b7280'
  return projectStore.speakerMap[speakerId]?.color ?? '#6b7280'
}

// ── Region management ──────────────────────────────────────────────────────
function rebuildRegions() {
  if (!regions || !isReady.value) return
  regions.clearRegions()
  regionSegMap.clear()

  for (const seg of projectStore.segments) {
    const color = speakerColor(seg.speaker_id)
    const region = regions.addRegion({
      id: String(seg.id),
      start: seg.start_ms / 1000,
      end: seg.end_ms / 1000,
      color: color + '33', // ~20% opacity
      drag: true,
      resize: true,
    })
    regionSegMap.set(region.id, seg.id)
  }

  // Re-register region-updated listener each time regions are rebuilt
  // (clearRegions removes all regions but the plugin-level listeners persist;
  //  we use a named handler to avoid stacking duplicates)
  regions.un('region-updated', onRegionUpdated)
  regions.on('region-updated', onRegionUpdated)
}

function onRegionUpdated(region: any) {
  isDragging.value = false

  const segId = regionSegMap.get(region.id)
  if (segId == null) return

  const start_ms = Math.round(region.start * 1000)
  const end_ms = Math.round(region.end * 1000)

  // Persist to backend
  projectStore.saveSegment(segId, { start_ms, end_ms })

  // Update the segment reactively in the local store so the subtitle list
  // stays consistent without a full reload
  const seg = projectStore.segments.find((s) => s.id === segId)
  if (seg) {
    seg.start_ms = start_ms
    seg.end_ms = end_ms
  }

  // Keep video playhead in sync with where the drag ended
  pipeline.seekTo(region.start * 1000)
  if (ws && isReady.value) {
    isSyncing = true
    ws.setTime(region.start)
    isSyncing = false
  }
}

// ── Play / Pause ───────────────────────────────────────────────────────────
function togglePlayPause() {
  ws?.playPause()
}

// ── WaveSurfer bootstrap ───────────────────────────────────────────────────
function initWaveSurfer() {
  if (!waveformEl.value) return

  regions = RegionsPlugin.create()

  ws = WaveSurfer.create({
    container: waveformEl.value,
    waveColor: '#4B5563',
    progressColor: '#3B82F6',
    cursorColor: '#60A5FA',
    height: WAVE_HEIGHT,
    normalize: true,
    backend: 'WebAudio' as any, // type accepted at runtime by WaveSurfer v7
    plugins: [regions],
  })

  // ── ready ──────────────────────────────────────────────────────────────
  ws.on('ready', () => {
    isLoading.value = false
    isReady.value = true
    duration.value = ws!.getDuration()
    rebuildRegions()
  })

  // ── error ──────────────────────────────────────────────────────────────
  ws.on('error', (_err: unknown) => {
    isLoading.value = false
    hasError.value = true
  })

  // ── timeupdate (fires during playback and after programmatic seeks) ─────
  ws.on('timeupdate', (currentTime: number) => {
    currentTimeSec.value = currentTime
    if (!isSyncing) {
      pipeline.seekTo(currentTime * 1000)
    }
  })

  // ── play / pause state ─────────────────────────────────────────────────
  ws.on('play', () => { playing.value = true })
  ws.on('pause', () => { playing.value = false })
  ws.on('finish', () => { playing.value = false })

  // ── region click ───────────────────────────────────────────────────────
  regions.on('region-clicked', (region: any, e: MouseEvent) => {
    e.stopPropagation()
    pipeline.seekTo(region.start * 1000)
    if (ws && isReady.value) {
      isSyncing = true
      ws.setTime(region.start)
      isSyncing = false
    }
  })

  // ── drag/resize start — set isDragging so the time-sync watcher backs off
  // WaveSurfer v7 RegionsPlugin has no region-update-start event, so we
  // detect mousedown on the waveform container instead.
  waveformEl.value.addEventListener('mousedown', () => {
    isDragging.value = true
  })
  // Safety net: always clear isDragging on mouseup (even if region-updated
  // doesn't fire, e.g. the user clicks without dragging).
  window.addEventListener('mouseup', () => {
    isDragging.value = false
  }, { passive: true })

  // ── load audio ─────────────────────────────────────────────────────────
  const audioUrl = `/api/audio/video/${props.projectId}`
  ws.load(audioUrl).catch(() => {
    isLoading.value = false
    hasError.value = true
  })
}

// ── Watchers ───────────────────────────────────────────────────────────────

// Sync external currentTimeMs → wavesurfer
watch(
  () => pipeline.currentTimeMs,
  (ms) => {
    // Skip while user is dragging/resizing a region so we don't fight them
    if (!ws || !isReady.value || isSyncing || isDragging.value) return
    const targetSec = ms / 1000
    // Only seek if the difference is meaningful (>150 ms) to avoid loops
    if (Math.abs(ws.getCurrentTime() - targetSec) > 0.15) {
      isSyncing = true
      ws.setTime(targetSec)
      currentTimeSec.value = targetSec
      isSyncing = false
    }
  },
)

// Rebuild regions whenever segments change
watch(
  () => projectStore.segments,
  () => rebuildRegions(),
  { deep: false }, // segments array reference changes on splice/push
)

// Also watch speakerMap so color changes are reflected
watch(
  () => projectStore.speakerMap,
  () => rebuildRegions(),
  { deep: true },
)

// ── Lifecycle ──────────────────────────────────────────────────────────────
onMounted(() => {
  initWaveSurfer()
})

onBeforeUnmount(() => {
  if (ws) {
    ws.destroy()
    ws = null
    regions = null
  }
})
</script>

<style scoped>
.waveform-timeline {
  position: relative;
  width: 100%;
  overflow: hidden;
}
</style>
