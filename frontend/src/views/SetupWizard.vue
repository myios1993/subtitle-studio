<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client'

const router = useRouter()

// ── State ──────────────────────────────────────────────────────────────────

interface SetupStatus {
  ffmpeg: { available: boolean; path: string; version: string | null }
  whisper: { downloaded: boolean; size: string; model_dir: string }
  pyannote: {
    all_downloaded: boolean
    download_running: boolean
    download_progress: number
    download_message: string
    download_error: string | null
    loaded: boolean
  }
  ready: boolean
}

const status = ref<SetupStatus | null>(null)
const loading = ref(true)
const whisperDownloading = ref(false)
const whisperError = ref('')
const pyannoteDownloading = ref(false)
const pyannoteError = ref('')
const hfToken = ref('')
const showToken = ref(false)
let pollTimer: ReturnType<typeof setInterval> | null = null

// ── Computed ───────────────────────────────────────────────────────────────

const ffmpegOk = computed(() => status.value?.ffmpeg.available ?? false)
const whisperOk = computed(() => status.value?.whisper.downloaded ?? false)
const pyannoteOk = computed(() => status.value?.pyannote.all_downloaded ?? false)
const allReady = computed(() => ffmpegOk.value && whisperOk.value)

// step 0=ffmpeg, 1=whisper, 2=pyannote, 3=done
const currentStep = computed(() => {
  if (!ffmpegOk.value) return 0
  if (!whisperOk.value) return 1
  if (!pyannoteOk.value) return 2
  return 3
})

// ── Lifecycle ──────────────────────────────────────────────────────────────

onMounted(async () => {
  await fetchStatus()
  // auto-proceed if already ready
  if (status.value?.ready) {
    router.replace('/')
    return
  }
  // poll while a download is running
  pollTimer = setInterval(fetchStatus, 2500)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})

// ── Methods ────────────────────────────────────────────────────────────────

async function fetchStatus() {
  try {
    status.value = await api.get<SetupStatus>('/setup/status')
    if (status.value?.ready && !whisperDownloading.value && !pyannoteDownloading.value) {
      // Ready — stop polling (user must click "Enter App")
    }
  } catch {
    // backend not up yet — ignore
  } finally {
    loading.value = false
  }
}

async function downloadWhisper() {
  whisperDownloading.value = true
  whisperError.value = ''
  try {
    // Use the ASR service via a dedicated endpoint (fires download in background)
    await api.post('/models/whisper/download', {})
    await fetchStatus()
  } catch (e: any) {
    whisperError.value = e.message ?? '下载失败'
  } finally {
    whisperDownloading.value = false
  }
}

async function downloadPyannote() {
  if (!hfToken.value.trim()) {
    pyannoteError.value = '请输入 HuggingFace Token'
    return
  }
  pyannoteDownloading.value = true
  pyannoteError.value = ''
  try {
    await api.post('/models/pyannote/download', { hf_token: hfToken.value.trim() })
    await fetchStatus()
  } catch (e: any) {
    pyannoteError.value = e.message ?? '下载失败'
    pyannoteDownloading.value = false
  }
}

function skipPyannote() {
  // pyannote is optional — proceed even without it
  if (pollTimer) clearInterval(pollTimer)
  router.replace('/')
}

function enterApp() {
  if (pollTimer) clearInterval(pollTimer)
  router.replace('/')
}
</script>

<template>
  <div class="min-h-screen bg-gray-950 flex items-center justify-center p-6">
    <div class="w-full max-w-2xl">
      <!-- Header -->
      <div class="text-center mb-10">
        <h1 class="text-3xl font-bold text-white mb-2">SubtitleStudio</h1>
        <p class="text-gray-400">首次运行设置向导</p>
      </div>

      <!-- Loading -->
      <div v-if="loading" class="flex justify-center py-12">
        <div class="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
      </div>

      <template v-else-if="status">
        <!-- Steps -->
        <div class="space-y-4">

          <!-- ── Step 1: ffmpeg ── -->
          <div class="rounded-xl border p-5 transition-colors"
               :class="ffmpegOk ? 'border-green-700 bg-green-900/20' : 'border-yellow-600 bg-yellow-900/20'">
            <div class="flex items-start gap-4">
              <div class="mt-0.5 text-xl">{{ ffmpegOk ? '✅' : '⚠️' }}</div>
              <div class="flex-1">
                <h3 class="font-semibold text-white">FFmpeg</h3>
                <p v-if="ffmpegOk" class="text-sm text-green-400 mt-1">
                  已检测到 ffmpeg {{ status.ffmpeg.version }}（{{ status.ffmpeg.path }}）
                </p>
                <template v-else>
                  <p class="text-sm text-yellow-300 mt-1">
                    未检测到 ffmpeg。视频文件导入和音频格式转换需要 ffmpeg。
                  </p>
                  <div class="mt-3 text-sm text-gray-300 space-y-1 bg-gray-900 rounded-lg p-3 font-mono">
                    <p class="text-gray-500"># 方式一：使用 winget（推荐）</p>
                    <p>winget install ffmpeg</p>
                    <p class="text-gray-500 mt-2"># 方式二：下载后加入 PATH</p>
                    <p>https://www.gyan.dev/ffmpeg/builds/</p>
                  </div>
                  <p class="text-xs text-gray-500 mt-2">
                    安装完成后重启本程序，或
                    <button @click="fetchStatus" class="text-blue-400 hover:underline">点此重新检测</button>
                  </p>
                </template>
              </div>
            </div>
          </div>

          <!-- ── Step 2: Whisper ── -->
          <div class="rounded-xl border p-5 transition-colors"
               :class="whisperOk
                 ? 'border-green-700 bg-green-900/20'
                 : currentStep === 1 ? 'border-blue-600 bg-blue-900/20' : 'border-gray-700 bg-gray-900/30'">
            <div class="flex items-start gap-4">
              <div class="mt-0.5 text-xl">{{ whisperOk ? '✅' : currentStep === 1 ? '📥' : '⬜' }}</div>
              <div class="flex-1">
                <h3 class="font-semibold text-white">
                  Whisper ASR 模型
                  <span class="text-gray-400 font-normal text-sm ml-1">({{ status.whisper.size }})</span>
                </h3>
                <p v-if="whisperOk" class="text-sm text-green-400 mt-1">模型已下载</p>
                <template v-else>
                  <p class="text-sm text-gray-300 mt-1">
                    语音识别核心模型，约 1.5 GB（medium）。首次下载需要网络连接。
                  </p>
                  <p class="text-xs text-gray-500 mt-1">
                    保存路径：{{ status.whisper.model_dir }}
                  </p>
                  <div v-if="currentStep === 1" class="mt-3 flex items-center gap-3">
                    <button
                      @click="downloadWhisper"
                      :disabled="whisperDownloading"
                      class="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded-lg transition-colors"
                    >
                      {{ whisperDownloading ? '下载中…' : '开始下载' }}
                    </button>
                    <p v-if="whisperError" class="text-red-400 text-sm">{{ whisperError }}</p>
                    <p class="text-xs text-gray-500">
                      也可在设置页手动选择模型大小
                    </p>
                  </div>
                </template>
              </div>
            </div>
          </div>

          <!-- ── Step 3: pyannote ── -->
          <div class="rounded-xl border p-5 transition-colors"
               :class="pyannoteOk
                 ? 'border-green-700 bg-green-900/20'
                 : currentStep === 2 ? 'border-purple-600 bg-purple-900/20' : 'border-gray-700 bg-gray-900/30'">
            <div class="flex items-start gap-4">
              <div class="mt-0.5 text-xl">{{ pyannoteOk ? '✅' : currentStep === 2 ? '📥' : '⬜' }}</div>
              <div class="flex-1">
                <h3 class="font-semibold text-white">说话人识别模型 <span class="text-gray-400 font-normal text-sm">(可选)</span></h3>
                <p v-if="pyannoteOk" class="text-sm text-green-400 mt-1">pyannote 模型已下载</p>
                <template v-else>
                  <p class="text-sm text-gray-300 mt-1">
                    pyannote.audio 用于区分不同说话人。需要 HuggingFace Token
                    并在 HF 网站接受模型许可。跳过后可在设置页随时下载。
                  </p>

                  <template v-if="currentStep === 2">
                    <!-- Download progress -->
                    <div v-if="status.pyannote.download_running" class="mt-3">
                      <div class="flex items-center gap-3 text-sm text-purple-300">
                        <div class="w-5 h-5 border-2 border-purple-400 border-t-transparent rounded-full animate-spin shrink-0" />
                        <span>{{ status.pyannote.download_message || '下载中…' }}</span>
                      </div>
                      <div class="mt-2 w-full h-2 bg-gray-800 rounded-full overflow-hidden">
                        <div class="h-full bg-purple-500 transition-all duration-500 rounded-full"
                             :style="{ width: `${status.pyannote.download_progress * 100}%` }" />
                      </div>
                      <p class="text-xs text-gray-500 mt-1">
                        {{ Math.round(status.pyannote.download_progress * 100) }}%
                      </p>
                    </div>

                    <div v-else class="mt-3 space-y-3">
                      <!-- HF Token input -->
                      <div>
                        <label class="text-xs text-gray-400 block mb-1">HuggingFace Token</label>
                        <div class="flex gap-2">
                          <input
                            v-model="hfToken"
                            :type="showToken ? 'text' : 'password'"
                            placeholder="hf_xxxxxxxxxxxx"
                            class="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-purple-500"
                          />
                          <button @click="showToken = !showToken"
                                  class="text-gray-500 hover:text-gray-300 px-2 text-xs">
                            {{ showToken ? '隐藏' : '显示' }}
                          </button>
                        </div>
                        <p class="text-xs text-gray-500 mt-1">
                          前往
                          <span class="text-purple-400">huggingface.co/settings/tokens</span>
                          生成 Read Token，并在
                          <span class="text-purple-400">pyannote/speaker-diarization-3.1</span>
                          页面同意许可协议
                        </p>
                      </div>

                      <div class="flex items-center gap-3">
                        <button
                          @click="downloadPyannote"
                          :disabled="pyannoteDownloading"
                          class="px-4 py-2 bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white text-sm rounded-lg transition-colors"
                        >
                          下载说话人识别模型
                        </button>
                        <button
                          @click="skipPyannote"
                          class="text-gray-400 hover:text-white text-sm transition-colors"
                        >
                          跳过
                        </button>
                      </div>
                      <p v-if="pyannoteError" class="text-red-400 text-sm">{{ pyannoteError }}</p>
                    </div>
                  </template>
                </template>
              </div>
            </div>
          </div>
        </div>

        <!-- Enter app button -->
        <div class="mt-8 flex justify-center">
          <button
            v-if="allReady || currentStep >= 2"
            @click="enterApp"
            class="px-8 py-3 bg-green-600 hover:bg-green-500 text-white font-semibold rounded-xl transition-colors shadow-lg"
          >
            {{ allReady ? '进入应用 →' : '跳过并进入应用 →' }}
          </button>
          <p v-else class="text-gray-500 text-sm">请完成上方必要步骤后继续</p>
        </div>
      </template>
    </div>
  </div>
</template>
