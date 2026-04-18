<script setup lang="ts">
import { ref, reactive, watch, onMounted, onUnmounted } from 'vue'
import { api } from '../api/client'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
type Provider = 'argos' | 'openai' | 'deepl' | 'compatible'
type TargetLang = 'zh' | 'en' | 'ja' | 'ko'

interface TranslationConfig {
  provider: Provider
  api_key: string
  base_url: string
  model: string
  target_lang: TargetLang
}

// The base api client has no `put`; add a thin helper here.
async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(data.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

// ---------------------------------------------------------------------------
// Section save-state helpers
// ---------------------------------------------------------------------------
interface SectionState {
  loading: boolean
  success: boolean
  error: string
  successTimer: ReturnType<typeof setTimeout> | null
}

function makeSectionState(): SectionState {
  return { loading: false, success: false, error: '', successTimer: null }
}

function showSuccess(s: SectionState) {
  s.success = true
  s.error = ''
  if (s.successTimer) clearTimeout(s.successTimer)
  s.successTimer = setTimeout(() => { s.success = false }, 2000)
}

// ---------------------------------------------------------------------------
// 1. Translation config state
// ---------------------------------------------------------------------------
const translationConfig = reactive<TranslationConfig>({
  provider: 'argos',
  api_key: '',
  base_url: '',
  model: 'gpt-4o-mini',
  target_lang: 'zh',
})
const translationState = reactive<SectionState>(makeSectionState())

const providerOptions: { value: Provider; label: string }[] = [
  { value: 'argos',      label: 'ArgosTranslate（离线，无需 API Key）' },
  { value: 'openai',     label: 'OpenAI API' },
  { value: 'deepl',      label: 'DeepL API' },
  { value: 'compatible', label: '兼容 OpenAI 协议' },
]

const targetLangOptions: { value: TargetLang; label: string }[] = [
  { value: 'zh', label: '中文' },
  { value: 'en', label: 'English' },
  { value: 'ja', label: '日本語' },
  { value: 'ko', label: '한국어' },
]

// Reset model default when switching providers
watch(() => translationConfig.provider, (provider) => {
  if (provider === 'openai' && !translationConfig.model) {
    translationConfig.model = 'gpt-4o-mini'
  }
})

async function saveTranslationConfig() {
  translationState.loading = true
  translationState.error = ''
  try {
    const payload: Partial<TranslationConfig> = {
      provider: translationConfig.provider,
      target_lang: translationConfig.target_lang,
    }
    if (translationConfig.provider !== 'argos') {
      payload.api_key = translationConfig.api_key
    }
    if (translationConfig.provider === 'openai' || translationConfig.provider === 'compatible') {
      payload.model = translationConfig.model
    }
    if (translationConfig.provider === 'compatible') {
      payload.base_url = translationConfig.base_url
    }
    await apiPut<unknown>('/settings/translation/config', payload)
    showSuccess(translationState)
  } catch (err: unknown) {
    translationState.error = err instanceof Error ? err.message : '保存失败'
  } finally {
    translationState.loading = false
  }
}

// ---------------------------------------------------------------------------
// Translation connection test
// ---------------------------------------------------------------------------
interface TestResult {
  loading: boolean
  ok: boolean | null   // null = not yet tested
  result: string
  error: string
}
const testResult = reactive<TestResult>({ loading: false, ok: null, result: '', error: '' })

async function testTranslationConfig() {
  testResult.loading = true
  testResult.ok = null
  testResult.result = ''
  testResult.error = ''
  try {
    const res = await fetch('/api/settings/translation/test', { method: 'POST' })
    const data = await res.json()
    testResult.ok = data.ok
    testResult.result = data.result || ''
    testResult.error = data.error || ''
  } catch (err: unknown) {
    testResult.ok = false
    testResult.error = err instanceof Error ? err.message : '请求失败'
  } finally {
    testResult.loading = false
  }
}

// ---------------------------------------------------------------------------
// 2. Model config state
// ---------------------------------------------------------------------------
const whisperModel = ref('base')
const whisperState = reactive<SectionState>(makeSectionState())

const hfToken = ref('')
const hfTokenSaved = ref(false)   // true = DB already has a token (displayed as placeholder)
const hfTokenState = reactive<SectionState>(makeSectionState())

const showHfToken = ref(false)

const whisperModelOptions = [
  { value: 'tiny',            label: 'Tiny — 最快，精度最低' },
  { value: 'base',            label: 'Base — 速度与精度均衡（推荐）' },
  { value: 'small',           label: 'Small — 中等精度' },
  { value: 'medium',          label: 'Medium — 较高精度' },
  { value: 'large-v3',        label: 'Large v3 — 最高精度，需大显存' },
  { value: 'distil-large-v3', label: 'Distil Large v3 — Large v3 的轻量版' },
]

async function saveWhisperModel() {
  whisperState.loading = true
  whisperState.error = ''
  try {
    await apiPut<unknown>('/settings/whisper_model_size', { value: whisperModel.value })
    showSuccess(whisperState)
  } catch (err: unknown) {
    whisperState.error = err instanceof Error ? err.message : '保存失败'
  } finally {
    whisperState.loading = false
  }
}

async function saveHfToken() {
  if (!hfToken.value.trim()) {
    hfTokenState.error = '请输入 Token'
    return
  }
  hfTokenState.loading = true
  hfTokenState.error = ''
  try {
    await apiPut<unknown>('/settings/hf_token', { value: hfToken.value.trim() })
    hfTokenSaved.value = true
    hfToken.value = ''   // clear after save (don't show plaintext in UI)
    showSuccess(hfTokenState)
  } catch (err: unknown) {
    hfTokenState.error = err instanceof Error ? err.message : '保存失败'
  } finally {
    hfTokenState.loading = false
  }
}

// ---------------------------------------------------------------------------
// 3. pyannote model management
// ---------------------------------------------------------------------------
interface PyannoteStatus {
  repos: Record<string, boolean>
  all_downloaded: boolean
  loaded: boolean
  download_running: boolean
  download_progress: number
  download_message: string
  download_error: string
}

const pyannoteStatus = reactive<PyannoteStatus>({
  repos: {},
  all_downloaded: false,
  loaded: false,
  download_running: false,
  download_progress: 0,
  download_message: '',
  download_error: '',
})

const downloadToken = ref('')
const showDownloadToken = ref(false)
const downloadError = ref('')
const loadingPyannote = ref(false)

let pollTimer: ReturnType<typeof setInterval> | null = null

async function fetchPyannoteStatus() {
  try {
    const data = await api.get<any>('/models/status')
    Object.assign(pyannoteStatus, {
      repos: data.pyannote.repos,
      all_downloaded: data.pyannote.all_downloaded,
      loaded: data.pyannote.loaded,
      download_running: data.pyannote.download_running,
      download_progress: data.pyannote.download_progress,
      download_message: data.pyannote.download_message,
      download_error: data.pyannote.download_error,
    })
    // Stop polling once download finishes
    if (!data.pyannote.download_running && pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  } catch { /* silently ignore status fetch errors */ }
}

async function startDownload() {
  if (!downloadToken.value.trim()) return
  downloadError.value = ''
  try {
    await fetch('/api/models/pyannote/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hf_token: downloadToken.value.trim() }),
    })
    // Start polling progress every 2s
    if (!pollTimer) {
      pollTimer = setInterval(fetchPyannoteStatus, 2000)
    }
  } catch (err: unknown) {
    downloadError.value = err instanceof Error ? err.message : '启动下载失败'
  }
}

async function loadPyannote() {
  loadingPyannote.value = true
  downloadError.value = ''
  try {
    const res = await fetch('/api/models/pyannote/load', { method: 'POST' })
    if (!res.ok) {
      const data = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
      throw new Error(data.detail || `HTTP ${res.status}`)
    }
    await fetchPyannoteStatus()
  } catch (err: unknown) {
    downloadError.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loadingPyannote.value = false
  }
}

// ---------------------------------------------------------------------------
// Mount: load current settings
// ---------------------------------------------------------------------------
const initError = ref('')

onMounted(async () => {
  try {
    // Load translation config
    const tConfig = await api.get<TranslationConfig>('/settings/translation/config')
    translationConfig.provider   = tConfig.provider   || 'argos'
    translationConfig.api_key    = tConfig.api_key === '***' ? '' : (tConfig.api_key || '')
    translationConfig.base_url   = tConfig.base_url   || ''
    translationConfig.model      = tConfig.model      || 'gpt-4o-mini'
    translationConfig.target_lang = tConfig.target_lang || 'zh'
  } catch {
    initError.value = '无法加载翻译配置'
  }

  try {
    // Load general settings (whisper model size, hf_token, etc.)
    const allSettings = await api.get<Record<string, string>>('/settings')
    if (allSettings.whisper_model_size) {
      whisperModel.value = allSettings.whisper_model_size
    }
    // hf_token is always masked as "***" in GET — just record whether one exists
    hfTokenSaved.value = !!(allSettings.hf_token && allSettings.hf_token !== '')
  } catch {
    // Non-fatal; model config section will use defaults
  }

  // Load pyannote status
  await fetchPyannoteStatus()

  // If download is already running, start polling
  if (pyannoteStatus.download_running) {
    pollTimer = setInterval(fetchPyannoteStatus, 2000)
  }
})

onUnmounted(() => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
})
</script>

<template>
  <div class="min-h-screen bg-gray-900 text-gray-100">
    <div class="max-w-2xl mx-auto px-4 py-10 space-y-12">

      <!-- Page title -->
      <div>
        <h1 class="text-2xl font-semibold tracking-tight">设置</h1>
        <p class="mt-1 text-sm text-gray-400">SubtitleStudio 应用配置</p>
        <p v-if="initError" class="mt-2 text-sm text-red-400">{{ initError }}</p>
      </div>

      <!-- ================================================================ -->
      <!-- Section 1: 翻译服务配置                                          -->
      <!-- ================================================================ -->
      <section>
        <div class="flex items-center gap-3 mb-6">
          <h2 class="text-base font-medium text-gray-100">翻译服务配置</h2>
          <div class="flex-1 h-px bg-gray-700" />
        </div>

        <div class="space-y-5">
          <!-- Provider selector -->
          <div>
            <label class="block text-sm font-medium text-gray-300 mb-2">翻译引擎</label>
            <div class="space-y-2">
              <label
                v-for="opt in providerOptions"
                :key="opt.value"
                class="flex items-center gap-3 px-3 py-2.5 rounded-lg border cursor-pointer transition-colors"
                :class="
                  translationConfig.provider === opt.value
                    ? 'border-indigo-500 bg-indigo-500/10'
                    : 'border-gray-700 bg-gray-800 hover:border-gray-600'
                "
              >
                <input
                  type="radio"
                  :value="opt.value"
                  v-model="translationConfig.provider"
                  class="accent-indigo-500"
                />
                <span class="text-sm text-gray-200">{{ opt.label }}</span>
              </label>
            </div>
          </div>

          <!-- Argos: info message -->
          <div
            v-if="translationConfig.provider === 'argos'"
            class="flex items-start gap-2 px-4 py-3 rounded-lg bg-blue-500/10 border border-blue-500/30 text-sm text-blue-300"
          >
            <svg class="w-4 h-4 mt-0.5 shrink-0" fill="currentColor" viewBox="0 0 20 20">
              <path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a.75.75 0 000 1.5h.253a.25.25 0 01.244.304l-.459 2.066A1.75 1.75 0 0010.747 15H11a.75.75 0 000-1.5h-.253a.25.25 0 01-.244-.304l.459-2.066A1.75 1.75 0 009.253 9H9z" clip-rule="evenodd" />
            </svg>
            <span>ArgosTranslate 为本地离线翻译，无需配置，可直接使用。</span>
          </div>

          <!-- Compatible: Base URL -->
          <div v-if="translationConfig.provider === 'compatible'">
            <label class="block text-sm font-medium text-gray-300 mb-1.5">Base URL</label>
            <input
              v-model="translationConfig.base_url"
              type="url"
              placeholder="https://your-endpoint.example.com/v1"
              class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
            />
          </div>

          <!-- OpenAI / DeepL / Compatible: API Key -->
          <div v-if="['openai', 'deepl', 'compatible'].includes(translationConfig.provider)">
            <label class="block text-sm font-medium text-gray-300 mb-1.5">API Key</label>
            <input
              v-model="translationConfig.api_key"
              type="password"
              autocomplete="off"
              placeholder="输入 API Key"
              class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
            />
          </div>

          <!-- OpenAI / Compatible: Model -->
          <div v-if="['openai', 'compatible'].includes(translationConfig.provider)">
            <label class="block text-sm font-medium text-gray-300 mb-1.5">模型</label>
            <input
              v-model="translationConfig.model"
              type="text"
              placeholder="gpt-4o-mini"
              class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
            />
          </div>

          <!-- Target language -->
          <div>
            <label class="block text-sm font-medium text-gray-300 mb-1.5">目标语言</label>
            <select
              v-model="translationConfig.target_lang"
              class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
            >
              <option v-for="opt in targetLangOptions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
          </div>

          <!-- Save row -->
          <div class="flex items-center gap-3 pt-1 flex-wrap">
            <button
              @click="saveTranslationConfig"
              :disabled="translationState.loading"
              class="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium transition-colors"
            >
              <svg
                v-if="translationState.loading"
                class="w-4 h-4 animate-spin"
                fill="none"
                viewBox="0 0 24 24"
              >
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
              </svg>
              保存翻译配置
            </button>

            <!-- Test connection button -->
            <button
              @click="testTranslationConfig"
              :disabled="testResult.loading"
              class="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium transition-colors"
            >
              <svg
                v-if="testResult.loading"
                class="w-4 h-4 animate-spin"
                fill="none"
                viewBox="0 0 24 24"
              >
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
              </svg>
              <svg v-else class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.348a1.125 1.125 0 010 1.971l-11.54 6.347a1.125 1.125 0 01-1.667-.985V5.653z" />
              </svg>
              测试连接
            </button>

            <Transition name="fade">
              <span
                v-if="translationState.success"
                class="flex items-center gap-1.5 text-sm text-green-400"
              >
                <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                </svg>
                已保存
              </span>
            </Transition>

            <span v-if="translationState.error" class="text-sm text-red-400">
              {{ translationState.error }}
            </span>
          </div>

          <!-- Test result banner -->
          <Transition name="fade">
            <div
              v-if="testResult.ok !== null"
              class="mt-3 px-4 py-3 rounded-lg border text-sm"
              :class="testResult.ok
                ? 'bg-green-500/10 border-green-500/30 text-green-300'
                : 'bg-red-500/10 border-red-500/30 text-red-300'"
            >
              <div class="flex items-start gap-2">
                <svg v-if="testResult.ok" class="w-4 h-4 mt-0.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                </svg>
                <svg v-else class="w-4 h-4 mt-0.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
                </svg>
                <div class="space-y-1">
                  <p v-if="testResult.ok" class="font-medium">
                    连接成功！翻译结果：<span class="font-mono">{{ testResult.result }}</span>
                  </p>
                  <p v-else class="font-medium">连接失败</p>
                  <p v-if="testResult.error" class="text-xs opacity-80">{{ testResult.error }}</p>
                </div>
              </div>
            </div>
          </Transition>
        </div>
      </section>

      <!-- ================================================================ -->
      <!-- Section 2: 模型配置                                               -->
      <!-- ================================================================ -->
      <section>
        <div class="flex items-center gap-3 mb-6">
          <h2 class="text-base font-medium text-gray-100">模型配置</h2>
          <div class="flex-1 h-px bg-gray-700" />
        </div>

        <div class="space-y-8">
          <!-- Whisper model -->
          <div class="space-y-3">
            <div>
              <label class="block text-sm font-medium text-gray-300 mb-0.5">Whisper 模型大小</label>
              <p class="text-xs text-gray-500">
                用于语音转录。模型越大，精度越高，但需要更多内存和更长处理时间。
              </p>
            </div>

            <select
              v-model="whisperModel"
              class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
            >
              <option
                v-for="opt in whisperModelOptions"
                :key="opt.value"
                :value="opt.value"
              >
                {{ opt.label }}
              </option>
            </select>

            <!-- Save row -->
            <div class="flex items-center gap-3">
              <button
                @click="saveWhisperModel"
                :disabled="whisperState.loading"
                class="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium transition-colors"
              >
                <svg
                  v-if="whisperState.loading"
                  class="w-4 h-4 animate-spin"
                  fill="none"
                  viewBox="0 0 24 24"
                >
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
                保存 Whisper 模型
              </button>

              <Transition name="fade">
                <span
                  v-if="whisperState.success"
                  class="flex items-center gap-1.5 text-sm text-green-400"
                >
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                  </svg>
                  已保存
                </span>
              </Transition>

              <span v-if="whisperState.error" class="text-sm text-red-400">
                {{ whisperState.error }}
              </span>
            </div>
          </div>

          <!-- HuggingFace Token -->
          <div class="space-y-3">
            <div>
              <label class="block text-sm font-medium text-gray-300 mb-0.5">HuggingFace Token</label>
              <p class="text-xs text-gray-500">
                用于 pyannote 说话人分离（Diarization）。需要在
                <a
                  href="https://huggingface.co/settings/tokens"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="text-indigo-400 hover:text-indigo-300 underline underline-offset-2"
                >huggingface.co</a>
                申请并接受 pyannote 模型许可协议。
              </p>
            </div>

            <!-- Already-saved indicator -->
            <p v-if="hfTokenSaved && !hfToken" class="text-xs text-green-400 mb-1.5 flex items-center gap-1">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12.75l6 6 9-13.5" />
              </svg>
              Token 已保存。输入新值可覆盖。
            </p>

            <div class="relative">
              <input
                v-model="hfToken"
                :type="showHfToken ? 'text' : 'password'"
                autocomplete="off"
                :placeholder="hfTokenSaved ? '输入新 Token 以覆盖' : 'hf_...'"
                class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 pr-10 text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
              />
              <button
                type="button"
                @click="showHfToken = !showHfToken"
                class="absolute inset-y-0 right-0 flex items-center px-3 text-gray-400 hover:text-gray-200 transition-colors"
                :aria-label="showHfToken ? '隐藏 Token' : '显示 Token'"
              >
                <!-- Eye icon -->
                <svg v-if="!showHfToken" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z" />
                  <path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
                <!-- Eye-slash icon -->
                <svg v-else class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88" />
                </svg>
              </button>
            </div>

            <!-- Save row -->
            <div class="flex items-center gap-3">
              <button
                @click="saveHfToken"
                :disabled="hfTokenState.loading"
                class="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium transition-colors"
              >
                <svg
                  v-if="hfTokenState.loading"
                  class="w-4 h-4 animate-spin"
                  fill="none"
                  viewBox="0 0 24 24"
                >
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
                保存 HF Token
              </button>

              <Transition name="fade">
                <span
                  v-if="hfTokenState.success"
                  class="flex items-center gap-1.5 text-sm text-green-400"
                >
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                  </svg>
                  已保存
                </span>
              </Transition>

              <span v-if="hfTokenState.error" class="text-sm text-red-400">
                {{ hfTokenState.error }}
              </span>
            </div>
          </div>
        </div>
      </section>

      <!-- ================================================================ -->
      <!-- Section 3: pyannote 说话人识别模型                               -->
      <!-- ================================================================ -->
      <section>
        <div class="flex items-center gap-3 mb-6">
          <h2 class="text-base font-medium text-gray-100">说话人识别模型（pyannote）</h2>
          <div class="flex-1 h-px bg-gray-700" />
        </div>

        <div class="space-y-5">
          <!-- Info -->
          <div class="flex items-start gap-2 px-4 py-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-sm text-amber-300">
            <svg class="w-4 h-4 mt-0.5 shrink-0" fill="currentColor" viewBox="0 0 20 20">
              <path fill-rule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd" />
            </svg>
            <span>
              pyannote 模型仅需下载一次，之后<strong>完全离线</strong>使用，无需 Token 和网络。
              首次下载需提供 HuggingFace Token（需接受
              <a href="https://huggingface.co/pyannote/speaker-diarization-3.1" target="_blank" rel="noopener" class="underline underline-offset-2 hover:text-amber-200">模型许可协议</a>）。
            </span>
          </div>

          <!-- Sub-model status cards -->
          <div class="space-y-2">
            <p class="text-xs font-medium text-gray-400 uppercase tracking-wider">子模型状态</p>
            <div
              v-for="(downloaded, repo) in pyannoteStatus.repos"
              :key="repo"
              class="flex items-center justify-between px-3 py-2 bg-gray-800 rounded-lg border border-gray-700"
            >
              <span class="text-sm text-gray-300 font-mono">{{ repo }}</span>
              <span
                class="text-xs px-2 py-0.5 rounded-full font-medium"
                :class="downloaded ? 'bg-green-500/15 text-green-400' : 'bg-gray-700 text-gray-500'"
              >
                {{ downloaded ? '✓ 已下载' : '未下载' }}
              </span>
            </div>
          </div>

          <!-- Loaded status -->
          <div class="flex items-center gap-2 text-sm">
            <span class="text-gray-400">运行状态：</span>
            <span
              class="px-2 py-0.5 rounded-full text-xs font-medium"
              :class="pyannoteStatus.loaded ? 'bg-green-500/15 text-green-400' : 'bg-gray-700 text-gray-500'"
            >
              {{ pyannoteStatus.loaded ? '✓ 已加载到内存' : '未加载' }}
            </span>
            <button
              v-if="pyannoteStatus.all_downloaded && !pyannoteStatus.loaded && !pyannoteStatus.download_running"
              @click="loadPyannote"
              :disabled="loadingPyannote"
              class="ml-2 text-xs px-2.5 py-1 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 transition-colors"
            >
              {{ loadingPyannote ? '加载中…' : '加载到内存' }}
            </button>
          </div>

          <!-- Download progress bar -->
          <div v-if="pyannoteStatus.download_running || pyannoteStatus.download_progress > 0 && !pyannoteStatus.all_downloaded" class="space-y-2">
            <div class="flex items-center justify-between text-sm">
              <span class="text-gray-300">{{ pyannoteStatus.download_message || '下载中…' }}</span>
              <span class="text-gray-400 tabular-nums">{{ Math.round(pyannoteStatus.download_progress * 100) }}%</span>
            </div>
            <div class="w-full h-2 bg-gray-700 rounded-full overflow-hidden">
              <div
                class="h-full bg-indigo-500 transition-all duration-500"
                :style="{ width: `${pyannoteStatus.download_progress * 100}%` }"
              />
            </div>
            <p v-if="pyannoteStatus.download_error" class="text-xs text-red-400">{{ pyannoteStatus.download_error }}</p>
          </div>

          <!-- Download section (when not all downloaded) -->
          <div v-if="!pyannoteStatus.all_downloaded" class="space-y-3 pt-1">
            <p class="text-sm text-gray-400">
              模型大小约 <strong class="text-gray-300">1.2 GB</strong>（三个子模型合计），下载完成后无需网络。
            </p>
            <div>
              <label class="block text-sm font-medium text-gray-300 mb-1.5">
                HuggingFace Token
                <span class="ml-1 text-gray-500 font-normal text-xs">（仅首次下载需要）</span>
              </label>
              <div class="relative">
                <input
                  v-model="downloadToken"
                  :type="showDownloadToken ? 'text' : 'password'"
                  placeholder="hf_..."
                  class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 pr-10 text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                />
                <button
                  type="button"
                  @click="showDownloadToken = !showDownloadToken"
                  class="absolute inset-y-0 right-0 flex items-center px-3 text-gray-400 hover:text-gray-200 transition-colors"
                >
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z" />
                    <path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                  </svg>
                </button>
              </div>
              <p class="mt-1.5 text-xs text-gray-500">
                Token 仅用于首次下载，不会长期存储。
                <a href="https://huggingface.co/settings/tokens" target="_blank" rel="noopener" class="text-indigo-400 hover:text-indigo-300 underline underline-offset-1">获取 Token →</a>
              </p>
            </div>

            <button
              @click="startDownload"
              :disabled="pyannoteStatus.download_running || !downloadToken.trim()"
              class="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium transition-colors"
            >
              <svg v-if="pyannoteStatus.download_running" class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
              </svg>
              <svg v-else class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
              </svg>
              {{ pyannoteStatus.download_running ? '下载中…' : '开始下载 pyannote 模型' }}
            </button>
            <p v-if="downloadError" class="text-sm text-red-400">{{ downloadError }}</p>
          </div>

          <!-- Already downloaded -->
          <div v-else class="flex items-center gap-2 text-sm text-green-400">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12.75l6 6 9-13.5" />
            </svg>
            所有子模型已下载到本地，后续无需网络连接
          </div>
        </div>
      </section>

      <!-- ================================================================ -->
      <!-- Section 4: 关于                                                   -->
      <!-- ================================================================ -->
      <section>
        <div class="flex items-center gap-3 mb-6">
          <h2 class="text-base font-medium text-gray-100">关于</h2>
          <div class="flex-1 h-px bg-gray-700" />
        </div>

        <div class="px-4 py-4 bg-gray-800 border border-gray-700 rounded-lg space-y-2">
          <p class="text-sm font-semibold text-gray-100">SubtitleStudio v0.1.0</p>
          <p class="text-sm text-gray-400 leading-relaxed">
            本地优先的字幕生成与翻译工具。支持离线语音识别（Whisper）、说话人分离（pyannote）及多引擎字幕翻译，所有处理均在本地完成，无需上传媒体文件。
          </p>
        </div>
      </section>

    </div>
  </div>
</template>

<style scoped>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
