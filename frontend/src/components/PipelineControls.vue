<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useProjectStore } from '../stores/project'
import { usePipelineStore } from '../stores/pipeline'
import { pipelineApi } from '../api/pipeline'
import type { AudioDevices } from '../api/types'

const store = useProjectStore()
const pipeline = usePipelineStore()

const devices = ref<AudioDevices | null>(null)
const selectedDevice = ref<number | undefined>(undefined)
const language = ref<string>('')
const numSpeakers = ref<number | undefined>(undefined)
const starting = ref(false)
const stopping = ref(false)
const resetting = ref(false)
const error = ref('')

const project = computed(() => store.project)
const isRunning = computed(() => project.value?.status === 'processing' || project.value?.status === 'capturing')
const canStart = computed(() => project.value && ['idle', 'done', 'error'].includes(project.value.status ?? ''))
const hasSegments = computed(() => (project.value?.segment_count ?? 0) > 0 || store.segments.length > 0)

onMounted(async () => {
  try {
    devices.value = await pipelineApi.getAudioDevices()
  } catch { /* ignore */ }
})

async function start() {
  if (!project.value || starting.value) return
  starting.value = true
  error.value = ''
  try {
    await pipelineApi.start(project.value.id, {
      device_index: selectedDevice.value,
      language: language.value || undefined,
      num_speakers: numSpeakers.value,
    })
    store.updateProjectStatus('processing')
    pipeline.setActive(true)
  } catch (e: any) {
    error.value = e.message
  } finally {
    starting.value = false
  }
}

async function stop() {
  if (!project.value || stopping.value) return
  stopping.value = true
  try {
    await pipelineApi.stop(project.value.id)
    pipeline.setActive(false)
  } catch (e: any) {
    error.value = e.message
  } finally {
    stopping.value = false
  }
}

async function rerun() {
  if (!project.value || resetting.value) return
  if (!confirm('重新运行将清空当前所有字幕，确定继续？')) return
  resetting.value = true
  error.value = ''
  try {
    await pipelineApi.reset(project.value.id)
    // Reload project state (clears segment count)
    await store.loadProject(project.value.id)
    pipeline.reset()
    // Auto-start
    await start()
  } catch (e: any) {
    error.value = e.message
  } finally {
    resetting.value = false
  }
}

async function translate() {
  if (!project.value || pipeline.translating) return
  error.value = ''
  try {
    await pipelineApi.translate(project.value.id)
    // pipeline.setTranslating(true) is set by the WebSocket translation_started event
  } catch (e: any) {
    error.value = e.message
  }
}
</script>

<template>
  <div class="flex items-center gap-3 flex-wrap">
    <!-- Device selector (mic/loopback modes) -->
    <template v-if="project?.capture_mode !== 'file' && devices">
      <select
        v-model="selectedDevice"
        class="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-sm text-gray-300 focus:outline-none focus:border-blue-500"
      >
        <option :value="undefined">默认设备</option>
        <optgroup v-if="project?.capture_mode === 'microphone'" label="麦克风">
          <option v-for="d in devices.microphones" :key="d.index" :value="d.index">
            {{ d.name }} ({{ d.sample_rate }}Hz)
          </option>
        </optgroup>
        <optgroup v-if="project?.capture_mode === 'loopback'" label="系统声音">
          <option v-for="d in devices.loopbacks" :key="d.index" :value="d.index">
            {{ d.name }}
          </option>
        </optgroup>
      </select>
    </template>

    <!-- Language selector -->
    <select
      v-model="language"
      class="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-sm text-gray-300 focus:outline-none focus:border-blue-500"
    >
      <option value="">自动检测语言</option>
      <option value="en">English</option>
      <option value="zh">中文</option>
      <option value="ja">日本語</option>
      <option value="ko">한국어</option>
    </select>

    <!-- Speaker count hint -->
    <div class="flex items-center gap-1.5">
      <label class="text-xs text-gray-500 whitespace-nowrap">说话人数</label>
      <input
        v-model.number="numSpeakers"
        type="number"
        min="1"
        max="20"
        placeholder="自动"
        class="w-16 bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-sm text-gray-300 focus:outline-none focus:border-blue-500"
        title="预设说话人数量，可提升说话人识别准确率，留空为自动"
      />
    </div>

    <!-- Start button -->
    <button
      v-if="canStart && !hasSegments"
      @click="start"
      :disabled="starting"
      class="px-4 py-1.5 bg-green-600 hover:bg-green-500 disabled:opacity-50 text-white text-sm rounded-lg transition-colors flex items-center gap-1.5"
    >
      <span v-if="starting" class="animate-spin inline-block">⏳</span>
      <span v-else>▶</span>
      开始处理
    </button>

    <!-- Re-run button (only when segments already exist) -->
    <button
      v-if="canStart && hasSegments"
      @click="rerun"
      :disabled="resetting || starting"
      class="px-4 py-1.5 bg-green-600 hover:bg-green-500 disabled:opacity-50 text-white text-sm rounded-lg transition-colors flex items-center gap-1.5"
      title="清空现有字幕并重新识别"
    >
      <span v-if="resetting || starting" class="animate-spin inline-block">⏳</span>
      <span v-else>↺</span>
      重新识别
    </button>

    <!-- Stop button -->
    <button
      v-if="isRunning"
      @click="stop"
      :disabled="stopping"
      class="px-4 py-1.5 bg-red-600 hover:bg-red-500 disabled:opacity-50 text-white text-sm rounded-lg transition-colors flex items-center gap-1.5"
    >
      ■ 停止
    </button>

    <!-- Translate button (only after ASR is done and project has segments) -->
    <button
      v-if="!isRunning && hasSegments && !pipeline.translating"
      @click="translate"
      class="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white text-sm rounded-lg transition-colors flex items-center gap-1.5"
      title="将所有字幕翻译为目标语言（在设置中配置翻译服务）"
    >
      <svg class="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor">
        <path fill-rule="evenodd" d="M7 2a1 1 0 011 1v1h3a1 1 0 110 2H9.578a18.87 18.87 0 01-1.724 4.78c.29.354.596.696.914 1.026a1 1 0 11-1.44 1.389 21.034 21.034 0 01-.554-.6 19.098 19.098 0 01-3.107 3.567 1 1 0 01-1.334-1.49 17.087 17.087 0 003.13-3.733 18.992 18.992 0 01-1.487-6.654H3a1 1 0 110-2h3V3a1 1 0 011-1zm6 6a1 1 0 01.894.553l2.991 5.992a.869.869 0 01.02.037l.99 1.98a1 1 0 11-1.79.895L15.383 16h-4.764l-.723 1.447a1 1 0 11-1.789-.894l.99-1.98.019-.038 2.99-5.992A1 1 0 0113 8zm-1.382 6h2.764L13 11.236 11.618 14z" clip-rule="evenodd" />
      </svg>
      翻译字幕
    </button>

    <!-- ASR Progress bar -->
    <div v-if="pipeline.active" class="flex items-center gap-2 text-sm text-gray-400">
      <div class="w-24 h-1.5 bg-gray-800 rounded-full overflow-hidden">
        <div class="h-full bg-blue-500 transition-all" :style="{ width: `${pipeline.progress * 100}%` }" />
      </div>
      <span>{{ Math.round(pipeline.progress * 100) }}%</span>
    </div>

    <!-- Translation progress bar -->
    <div v-if="pipeline.translating" class="flex items-center gap-2 text-sm text-indigo-400">
      <svg class="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
      </svg>
      <div class="w-24 h-1.5 bg-gray-800 rounded-full overflow-hidden">
        <div class="h-full bg-indigo-500 transition-all" :style="{ width: `${pipeline.translationProgress * 100}%` }" />
      </div>
      <span>翻译中 {{ Math.round(pipeline.translationProgress * 100) }}%</span>
    </div>

    <!-- Local (HTTP) error -->
    <span v-if="error" class="text-red-400 text-sm">{{ error }}</span>

    <!-- Translation error from WebSocket (fatal) -->
    <span
      v-if="!pipeline.translating && pipeline.translationError"
      class="text-red-400 text-sm flex items-center gap-1"
      :title="pipeline.translationError"
    >
      <svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
      </svg>
      翻译失败：{{ pipeline.translationError.slice(0, 60) }}{{ pipeline.translationError.length > 60 ? '…' : '' }}
    </span>

    <!-- Translation warning from WebSocket (non-fatal, e.g. partial failure) -->
    <span
      v-if="pipeline.translationWarning"
      class="text-amber-400 text-sm flex items-center gap-1"
      :title="pipeline.translationWarning"
    >
      <svg class="w-3.5 h-3.5 shrink-0" fill="currentColor" viewBox="0 0 20 20">
        <path fill-rule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd" />
      </svg>
      {{ pipeline.translationWarning.slice(0, 60) }}{{ pipeline.translationWarning.length > 60 ? '…' : '' }}
    </span>
  </div>
</template>
