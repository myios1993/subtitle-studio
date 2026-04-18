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
const sourceLang = ref<string>('')  // source language for translation
const starting = ref(false)
const stopping = ref(false)
const resetting = ref(false)
const resuming = ref(false)
const error = ref('')

const project = computed(() => store.project)
const isRunning = computed(() => project.value?.status === 'processing' || project.value?.status === 'capturing')
const canStart = computed(() => project.value && ['idle', 'done', 'error'].includes(project.value.status ?? ''))
const hasSegments = computed(() => (project.value?.segment_count ?? 0) > 0 || store.segments.length > 0)
const isFileMode = computed(() => project.value?.capture_mode === 'file')

const languageOptions = [
  { value: '',   label: '自动检测语言' },
  { value: 'en', label: 'English' },
  { value: 'zh', label: '中文' },
  { value: 'ja', label: '日本語' },
  { value: 'ko', label: '한국어' },
]

const sourceLangOptions = [
  { value: '',   label: '自动检测' },
  { value: 'en', label: '英语' },
  { value: 'zh', label: '中文' },
  { value: 'ja', label: '日语' },
  { value: 'ko', label: '韩语' },
]

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
  if (!confirm('重新识别将清空当前所有字幕和翻译，确定继续？')) return
  resetting.value = true
  error.value = ''
  try {
    await pipelineApi.reset(project.value.id)
    store.$reset()
    await store.loadProject(project.value.id)
    pipeline.reset()
    await start()
  } catch (e: any) {
    error.value = e.message
  } finally {
    resetting.value = false
  }
}

async function resumeAsr() {
  if (!project.value || resuming.value) return
  resuming.value = true
  error.value = ''
  try {
    await pipelineApi.resume(project.value.id, {
      language: language.value || undefined,
      num_speakers: numSpeakers.value,
    })
    store.updateProjectStatus('processing')
    pipeline.setActive(true)
  } catch (e: any) {
    error.value = e.message
  } finally {
    resuming.value = false
  }
}

async function translate() {
  if (!project.value || pipeline.translating) return
  error.value = ''
  pipeline.setTranslationError('')
  pipeline.setTranslationWarning('')
  try {
    await pipelineApi.translateWithLang(project.value.id, sourceLang.value || undefined)
  } catch (e: any) {
    error.value = e.message
  }
}
</script>

<template>
  <div class="flex items-center gap-2 flex-wrap">

    <!-- Device selector (mic/loopback modes) -->
    <template v-if="project?.capture_mode !== 'file' && devices">
      <select
        v-model="selectedDevice"
        class="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-sm text-gray-300 focus:outline-none focus:border-blue-500"
        title="录音设备"
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

    <!-- Language selector for ASR -->
    <select
      v-model="language"
      class="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-sm text-gray-300 focus:outline-none focus:border-blue-500"
      title="识别语言"
    >
      <option v-for="opt in languageOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
    </select>

    <!-- Speaker count hint -->
    <div class="flex items-center gap-1.5" title="预设说话人数量，留空为自动">
      <label class="text-xs text-gray-500 whitespace-nowrap">说话人数</label>
      <input
        v-model.number="numSpeakers"
        type="number"
        min="1"
        max="20"
        placeholder="自动"
        class="w-14 bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-sm text-gray-300 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- ── Start button (no segments yet) ── -->
    <button
      v-if="canStart && !hasSegments"
      @click="start"
      :disabled="starting"
      class="px-3 py-1.5 bg-green-600 hover:bg-green-500 disabled:opacity-50 text-white text-sm rounded-lg transition-colors flex items-center gap-1.5"
      title="开始识别音频内容"
    >
      <svg v-if="starting" class="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
      </svg>
      <svg v-else class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.348a1.125 1.125 0 010 1.971l-11.54 6.347a1.125 1.125 0 01-1.667-.985V5.653z"/>
      </svg>
      开始识别
    </button>

    <!-- ── Resume button (file mode with existing segments) ── -->
    <button
      v-if="canStart && hasSegments && isFileMode"
      @click="resumeAsr"
      :disabled="resuming"
      class="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded-lg transition-colors flex items-center gap-1.5"
      title="从上次中断位置继续识别（不清除已有字幕）"
    >
      <svg v-if="resuming" class="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
      </svg>
      <svg v-else class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M3 8.689c0-.864.933-1.406 1.683-.977l7.108 4.061a1.125 1.125 0 010 1.954l-7.108 4.061A1.125 1.125 0 013 16.811V8.69zM12.75 8.689c0-.864.933-1.406 1.683-.977l7.108 4.061a1.125 1.125 0 010 1.954l-7.108 4.061a1.125 1.125 0 01-1.683-.977V8.69z"/>
      </svg>
      继续识别
    </button>

    <!-- ── Re-run button (clear and restart) ── -->
    <button
      v-if="canStart && hasSegments"
      @click="rerun"
      :disabled="resetting || starting"
      class="px-3 py-1.5 bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-white text-sm rounded-lg transition-colors flex items-center gap-1.5"
      title="清空现有字幕并从头重新识别"
    >
      <svg v-if="resetting || starting" class="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
      </svg>
      <svg v-else class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"/>
      </svg>
      重新识别
    </button>

    <!-- ── Stop button ── -->
    <button
      v-if="isRunning"
      @click="stop"
      :disabled="stopping"
      class="px-3 py-1.5 bg-red-600 hover:bg-red-500 disabled:opacity-50 text-white text-sm rounded-lg transition-colors flex items-center gap-1.5"
    >
      <svg class="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
        <rect x="6" y="6" width="12" height="12" rx="1"/>
      </svg>
      停止
    </button>

    <!-- Divider before translation controls -->
    <div v-if="!isRunning && hasSegments" class="w-px h-5 bg-gray-700 shrink-0" />

    <!-- Source language (for translation) -->
    <select
      v-if="!isRunning && hasSegments"
      v-model="sourceLang"
      class="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-sm text-gray-300 focus:outline-none focus:border-indigo-500"
      title="翻译时的源语言（影响翻译引擎的处理方式）"
    >
      <option v-for="opt in sourceLangOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
    </select>

    <!-- ── Translate button ── -->
    <button
      v-if="!isRunning && hasSegments && !pipeline.translating"
      @click="translate"
      class="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white text-sm rounded-lg transition-colors flex items-center gap-1.5"
      title="翻译所有未翻译的字幕段落（不影响已有译文）"
    >
      <svg class="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor">
        <path fill-rule="evenodd" d="M7 2a1 1 0 011 1v1h3a1 1 0 110 2H9.578a18.87 18.87 0 01-1.724 4.78c.29.354.596.696.914 1.026a1 1 0 11-1.44 1.389 21.034 21.034 0 01-.554-.6 19.098 19.098 0 01-3.107 3.567 1 1 0 01-1.334-1.49 17.087 17.087 0 003.13-3.733 18.992 18.992 0 01-1.487-6.654H3a1 1 0 110-2h3V3a1 1 0 011-1zm6 6a1 1 0 01.894.553l2.991 5.992a.869.869 0 01.02.037l.99 1.98a1 1 0 11-1.79.895L15.383 16h-4.764l-.723 1.447a1 1 0 11-1.789-.894l.99-1.98.019-.038 2.99-5.992A1 1 0 0113 8zm-1.382 6h2.764L13 11.236 11.618 14z" clip-rule="evenodd" />
      </svg>
      翻译字幕
    </button>

    <!-- ── ASR progress bar ── -->
    <div v-if="pipeline.active" class="flex items-center gap-1.5 text-xs text-blue-400">
      <div class="w-20 h-1.5 bg-gray-800 rounded-full overflow-hidden">
        <div class="h-full bg-blue-500 transition-all" :style="{ width: `${pipeline.progress * 100}%` }" />
      </div>
      <span class="tabular-nums">识别进度 {{ Math.round(pipeline.progress * 100) }}%</span>
    </div>

    <!-- ── Translation progress bar ── -->
    <div v-if="pipeline.translating" class="flex items-center gap-1.5 text-xs text-indigo-400">
      <svg class="w-3 h-3 animate-spin" viewBox="0 0 24 24" fill="none">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
      </svg>
      <div class="w-20 h-1.5 bg-gray-800 rounded-full overflow-hidden">
        <div class="h-full bg-indigo-500 transition-all" :style="{ width: `${pipeline.translationProgress * 100}%` }" />
      </div>
      <span class="tabular-nums">翻译进度 {{ Math.round(pipeline.translationProgress * 100) }}%</span>
    </div>

    <!-- ── Error messages ── -->
    <span v-if="error" class="text-red-400 text-xs">{{ error }}</span>

    <span
      v-if="!pipeline.translating && pipeline.translationError"
      class="text-red-400 text-xs flex items-center gap-1"
      :title="pipeline.translationError"
    >
      <svg class="w-3 h-3 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z"/>
      </svg>
      翻译失败：{{ pipeline.translationError.slice(0, 50) }}{{ pipeline.translationError.length > 50 ? '…' : '' }}
    </span>

    <span
      v-if="pipeline.translationWarning"
      class="text-amber-400 text-xs flex items-center gap-1"
      :title="pipeline.translationWarning"
    >
      <svg class="w-3 h-3 shrink-0" fill="currentColor" viewBox="0 0 20 20">
        <path fill-rule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd"/>
      </svg>
      {{ pipeline.translationWarning.slice(0, 50) }}{{ pipeline.translationWarning.length > 50 ? '…' : '' }}
    </span>

  </div>
</template>
