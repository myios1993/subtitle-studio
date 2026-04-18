<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '../stores/project'
import { usePipelineStore } from '../stores/pipeline'
import { useWebSocket } from '../composables/useWebSocket'
import { projectsApi } from '../api/projects'
import type { WsEvent, Speaker } from '../api/types'
import VideoPlayer from '../components/VideoPlayer.vue'
import SubtitleEditor from '../components/SubtitleEditor.vue'
import PipelineControls from '../components/PipelineControls.vue'
import ExportButton from '../components/ExportButton.vue'
import { segmentsApi } from '../api/segments'

// ---------------------------------------------------------------------------
// Props / Router
// ---------------------------------------------------------------------------
const props = defineProps<{ id: string }>()
const router = useRouter()

// ---------------------------------------------------------------------------
// Stores
// ---------------------------------------------------------------------------
const store = useProjectStore()
const pipeline = usePipelineStore()

// ---------------------------------------------------------------------------
// Computed shortcuts
// ---------------------------------------------------------------------------
const project = computed(() => store.project)
const projectId = computed(() => Number(props.id))

const hasVideo = computed(() =>
  !!(project.value?.playback_video_path || project.value?.source_video_path)
)

const needsUpload = computed(() =>
  project.value?.capture_mode === 'file' &&
  !project.value?.source_audio_path &&
  !project.value?.source_video_path &&
  project.value?.status === 'idle'
)

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------
const statusConfig = computed(() => {
  const s = project.value?.status ?? 'idle'
  const configs: Record<string, { label: string; classes: string }> = {
    idle:       { label: '空闲',   classes: 'bg-gray-700 text-gray-300' },
    capturing:  { label: '录音中', classes: 'bg-yellow-600/30 text-yellow-400 animate-pulse' },
    processing: { label: '处理中', classes: 'bg-blue-600/30 text-blue-400 animate-pulse' },
    done:       { label: '完成',   classes: 'bg-green-600/30 text-green-400' },
    error:      { label: '错误',   classes: 'bg-red-600/30 text-red-400' },
  }
  return configs[s] ?? configs.idle
})

// ---------------------------------------------------------------------------
// Error state
// ---------------------------------------------------------------------------
const loadError = ref('')

// ---------------------------------------------------------------------------
// File upload
// ---------------------------------------------------------------------------
const uploadProgress = ref(0)
const uploading = ref(false)
const uploadError = ref('')
const dragOver = ref(false)
const fileInputEl = ref<HTMLInputElement | null>(null)

const ACCEPTED_TYPES = [
  'video/mp4', 'video/webm', 'video/ogg', 'video/quicktime', 'video/x-msvideo',
  'audio/mpeg', 'audio/wav', 'audio/ogg', 'audio/mp4', 'audio/aac', 'audio/flac',
]

function triggerFileInput() {
  fileInputEl.value?.click()
}

function onFileInputChange(e: Event) {
  const files = (e.target as HTMLInputElement).files
  if (files && files.length > 0) {
    handleFile(files[0])
  }
}

function onDrop(e: DragEvent) {
  dragOver.value = false
  const files = e.dataTransfer?.files
  if (files && files.length > 0) {
    handleFile(files[0])
  }
}

async function handleFile(file: File) {
  if (!ACCEPTED_TYPES.includes(file.type) && !file.name.match(/\.(mp4|webm|ogv|mov|avi|mp3|wav|ogg|m4a|aac|flac)$/i)) {
    uploadError.value = '不支持的文件格式，请上传视频或音频文件'
    return
  }

  uploadError.value = ''
  uploading.value = true
  uploadProgress.value = 0

  const formData = new FormData()
  formData.append('file', file)

  try {
    await new Promise<void>((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open('POST', `/api/projects/${projectId.value}/upload`)

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          uploadProgress.value = Math.round((e.loaded / e.total) * 100)
        }
      }

      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve()
        } else {
          try {
            const body = JSON.parse(xhr.responseText)
            reject(new Error(body.detail || `HTTP ${xhr.status}`))
          } catch {
            reject(new Error(`HTTP ${xhr.status}`))
          }
        }
      }

      xhr.onerror = () => reject(new Error('网络错误，上传失败'))
      xhr.send(formData)
    })

    // Reload project to pick up new paths
    await store.loadProject(projectId.value)
  } catch (err: any) {
    uploadError.value = err.message ?? '上传失败'
  } finally {
    uploading.value = false
    if (fileInputEl.value) fileInputEl.value.value = ''
  }
}

// ---------------------------------------------------------------------------
// Speaker editing modal
// ---------------------------------------------------------------------------
const speakerEditing = ref<Speaker | null>(null)
const speakerEditLabel = ref('')
const speakerEditColor = ref('')
const speakerSaving = ref(false)

function openSpeakerModal(speaker: Speaker) {
  speakerEditing.value = speaker
  speakerEditLabel.value = speaker.label ?? speaker.speaker_id
  speakerEditColor.value = speaker.color ?? '#6b7280'
}

function closeSpeakerModal() {
  speakerEditing.value = null
}

async function saveSpeaker() {
  if (!speakerEditing.value || speakerSaving.value) return
  speakerSaving.value = true
  try {
    await projectsApi.updateSpeaker(projectId.value, speakerEditing.value.speaker_id, {
      label: speakerEditLabel.value.trim() || undefined,
      color: speakerEditColor.value || undefined,
    })
    // Refresh project so speakers list updates
    await store.loadProject(projectId.value)
    closeSpeakerModal()
  } catch (err: any) {
    // Silently ignore for now; the field will just not update
    console.error('Speaker update failed:', err)
  } finally {
    speakerSaving.value = false
  }
}

// Speaker inline save on blur
const speakerInlineLabels = ref<Record<string, string>>({})

function initInlineLabels() {
  const map: Record<string, string> = {}
  for (const sp of project.value?.speakers ?? []) {
    map[sp.speaker_id] = sp.label ?? sp.speaker_id
  }
  speakerInlineLabels.value = map
}

async function onSpeakerLabelBlur(speaker: Speaker) {
  const newLabel = (speakerInlineLabels.value[speaker.speaker_id] ?? '').trim()
  if (newLabel === (speaker.label ?? speaker.speaker_id)) return
  try {
    await projectsApi.updateSpeaker(projectId.value, speaker.speaker_id, { label: newLabel || undefined })
    await store.loadProject(projectId.value)
  } catch (err) {
    console.error('Speaker inline update failed:', err)
    // Restore original
    speakerInlineLabels.value[speaker.speaker_id] = speaker.label ?? speaker.speaker_id
  }
}

// ---------------------------------------------------------------------------
// Empty-segment cleanup prompt
// ---------------------------------------------------------------------------
async function promptDeleteEmptySegments(field: 'original_text' | 'translated_text') {
  if (!store.project) return
  const label = field === 'original_text' ? '识别文字' : '译文'
  const emptySegs = store.segments.filter(s =>
    field === 'original_text'
      ? !s.original_text?.trim()
      : !s.translated_text?.trim()
  )
  if (emptySegs.length === 0) return

  const yes = confirm(
    `处理完成后仍有 ${emptySegs.length} 条字幕的【${label}】为空（有时间点但无内容），` +
    `可能是识别错误或识别置信度过低。\n\n是否删除这些空白字幕？`
  )
  if (!yes) return

  try {
    const res = await segmentsApi.deleteEmpty(store.project.id, field)
    if (res.deleted > 0) {
      store.removeSegmentsByIds(emptySegs.map(s => s.id))
    }
  } catch (e) {
    console.error('删除空白字幕失败', e)
  }
}

// ---------------------------------------------------------------------------
// VideoPlayer ref for seekTo coordination
// ---------------------------------------------------------------------------
function onVideoTimeUpdate(_ms: number) {
  // pipeline store is already updated from inside VideoPlayer via pipeline.seekTo()
}

// ---------------------------------------------------------------------------
// WebSocket event handling
// ---------------------------------------------------------------------------
function handleWsEvent(e: WsEvent) {
  switch (e.event) {
    case 'segment_created':
      // Backend sends segment fields directly as e.data (not nested)
      if (e.data?.id) store.addSegment(e.data)
      break
    case 'segment_updated':
      // Backend sends a partial segment (id + changed fields) as e.data
      if (e.data?.id) store.patchSegment(e.data)
      break
    case 'pipeline_status':
      pipeline.setActive(true)
      if (typeof e.data?.progress === 'number') {
        pipeline.setProgress(e.data.progress, e.data.chunks_processed)
      }
      break
    case 'pipeline_done':
      pipeline.setActive(false)
      store.updateProjectStatus('done')
      store.loadProject(projectId.value).catch(() => {})
      // Reload segments then prompt about empty ones
      store.loadSegments().then(() => promptDeleteEmptySegments('original_text')).catch(() => {})
      break
    case 'pipeline_error':
      pipeline.setActive(false)
      store.updateProjectStatus('error')
      store.loadProject(projectId.value).catch(() => {})
      break
    case 'diarization_done':
      // Reload segments so speaker labels appear
      store.loadSegments().catch(() => {})
      // Also reload project so speakers panel updates
      store.loadProject(projectId.value).catch(() => {})
      break
    case 'translation_started':
      pipeline.setTranslating(true, 0)
      break
    case 'translation_progress':
      pipeline.setTranslating(true, e.data?.progress ?? 0)
      break
    case 'translation_done':
      pipeline.setTranslating(false, 1)
      store.loadSegments().then(() => promptDeleteEmptySegments('translated_text')).catch(() => {})
      break
    case 'translation_error':
      pipeline.setTranslating(false, 0)
      pipeline.setTranslationError(e.data?.error || '翻译失败')
      break
    case 'translation_warning':
      pipeline.setTranslationWarning(e.data?.warning || e.data?.error || '翻译出现警告')
      break
  }
}

const { connected, connect, disconnect } = useWebSocket(projectId.value, handleWsEvent)

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------
onMounted(async () => {
  // Reset both stores so a previous project's state doesn't bleed in
  store.$reset()
  pipeline.reset()
  try {
    await store.loadProject(projectId.value)
    initInlineLabels()
    // Sync pipeline store with actual project state
    if (project.value?.status === 'processing') {
      pipeline.setActive(true)
    }
  } catch (err: any) {
    loadError.value = err.message ?? '项目加载失败'
  }
  connect()
})

onUnmounted(() => {
  disconnect()
  store.$reset()
})
</script>

<template>
  <div class="h-screen bg-gray-900 text-white flex flex-col overflow-hidden">

    <!-- ======================================================================
         HEADER BAR
    ====================================================================== -->
    <header class="flex-shrink-0 bg-gray-800 border-b border-gray-700 px-4 py-3">
      <div class="flex items-center gap-3 flex-wrap">

        <!-- Back button -->
        <button
          @click="router.push('/')"
          class="flex items-center gap-1.5 text-gray-400 hover:text-white transition-colors text-sm"
          title="返回项目列表"
        >
          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M15 19l-7-7 7-7"/>
          </svg>
          返回
        </button>

        <div class="w-px h-5 bg-gray-700" />

        <!-- Project name + status -->
        <div class="flex items-center gap-2 min-w-0">
          <h1 class="text-base font-semibold text-white truncate max-w-xs">
            {{ project?.name ?? '加载中...' }}
          </h1>
          <span
            v-if="project"
            class="text-xs px-2 py-0.5 rounded-full font-medium flex-shrink-0"
            :class="statusConfig.classes"
          >
            {{ statusConfig.label }}
          </span>
        </div>

        <!-- WS indicator -->
        <div
          class="flex items-center gap-1.5 text-xs flex-shrink-0"
          :class="connected ? 'text-green-400' : 'text-gray-600'"
          :title="connected ? '实时连接正常' : '实时连接断开'"
        >
          <span class="w-1.5 h-1.5 rounded-full" :class="connected ? 'bg-green-400' : 'bg-gray-600'" />
          <span class="hidden sm:inline">{{ connected ? '实时' : '离线' }}</span>
        </div>

        <div class="flex-1" />

        <!-- Pipeline controls + Export -->
        <div class="flex items-center gap-2 flex-wrap justify-end">
          <PipelineControls v-if="project" />
          <ExportButton v-if="project" />
        </div>
      </div>

      <!-- Progress bar (pipeline active) -->
      <div v-if="pipeline.active" class="mt-2 h-0.5 bg-gray-700 rounded-full overflow-hidden">
        <div
          class="h-full bg-blue-500 transition-all duration-300"
          :style="{ width: `${pipeline.progress * 100}%` }"
        />
      </div>
    </header>

    <!-- ======================================================================
         LOAD ERROR
    ====================================================================== -->
    <div v-if="loadError" class="flex-1 flex items-center justify-center p-8">
      <div class="bg-red-900/30 border border-red-700 rounded-xl p-6 max-w-md text-center space-y-3">
        <svg class="w-10 h-10 text-red-400 mx-auto" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
          <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"/>
        </svg>
        <p class="text-red-300 font-medium">项目加载失败</p>
        <p class="text-gray-400 text-sm">{{ loadError }}</p>
        <button
          @click="router.push('/')"
          class="mt-2 px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg text-sm transition-colors"
        >
          返回首页
        </button>
      </div>
    </div>

    <!-- ======================================================================
         LOADING SKELETON
    ====================================================================== -->
    <div v-else-if="store.loading && !project" class="flex-1 flex items-center justify-center">
      <div class="flex flex-col items-center gap-4 text-gray-500">
        <svg class="w-8 h-8 animate-spin" viewBox="0 0 24 24" fill="none">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 100 16v-4l-3 3 3 3v-4a8 8 0 01-8-8z"/>
        </svg>
        <span class="text-sm">加载项目...</span>
      </div>
    </div>

    <!-- ======================================================================
         MAIN CONTENT
    ====================================================================== -->
    <div v-else-if="project" class="flex-1 flex overflow-hidden min-h-0">

      <!-- ============================================================
           FILE UPLOAD ZONE (file mode, no media yet)
      ============================================================ -->
      <div v-if="needsUpload" class="flex-1 flex items-center justify-center p-8">
        <div class="w-full max-w-lg space-y-4">
          <div
            class="border-2 border-dashed rounded-2xl p-12 text-center transition-colors cursor-pointer"
            :class="dragOver
              ? 'border-blue-500 bg-blue-500/10'
              : 'border-gray-600 hover:border-gray-500 bg-gray-800/50'"
            @click="triggerFileInput"
            @dragover.prevent="dragOver = true"
            @dragleave="dragOver = false"
            @drop.prevent="onDrop"
          >
            <input
              ref="fileInputEl"
              type="file"
              accept="video/*,audio/*,.mp4,.webm,.ogv,.mov,.avi,.mp3,.wav,.ogg,.m4a,.aac,.flac"
              class="hidden"
              @change="onFileInputChange"
            />

            <!-- Upload icon -->
            <svg
              class="w-14 h-14 mx-auto mb-4 transition-colors"
              :class="dragOver ? 'text-blue-400' : 'text-gray-600'"
              fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.2"
            >
              <path stroke-linecap="round" stroke-linejoin="round"
                d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"/>
            </svg>

            <p class="text-gray-300 font-medium mb-1">
              {{ dragOver ? '松开以上传文件' : '拖拽或点击上传媒体文件' }}
            </p>
            <p class="text-gray-500 text-sm">
              支持 MP4、WebM、MOV、AVI、MP3、WAV、AAC、FLAC 等格式
            </p>
          </div>

          <!-- Upload progress -->
          <div v-if="uploading" class="space-y-2">
            <div class="flex justify-between text-sm text-gray-400">
              <span>上传中...</span>
              <span>{{ uploadProgress }}%</span>
            </div>
            <div class="h-2 bg-gray-700 rounded-full overflow-hidden">
              <div
                class="h-full bg-blue-500 transition-all"
                :style="{ width: `${uploadProgress}%` }"
              />
            </div>
          </div>

          <!-- Upload error -->
          <div
            v-if="uploadError"
            class="flex items-center gap-2 p-3 bg-red-900/30 border border-red-700/50 rounded-lg text-sm text-red-400"
          >
            <svg class="w-4 h-4 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
              <path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/>
            </svg>
            {{ uploadError }}
          </div>
        </div>
      </div>

      <!-- ============================================================
           MAIN EDITOR LAYOUT
      ============================================================ -->
      <template v-else>

        <!-- LEFT SIDEBAR: Speakers panel -->
        <aside
          v-if="project.speakers && project.speakers.length > 0"
          class="w-44 flex-shrink-0 bg-gray-800 border-r border-gray-700 flex flex-col overflow-hidden hidden lg:flex"
        >
          <div class="px-3 py-2.5 border-b border-gray-700">
            <h2 class="text-xs font-semibold text-gray-400 uppercase tracking-wider">说话人</h2>
          </div>
          <div class="flex-1 overflow-y-auto p-2 space-y-1">
            <div
              v-for="speaker in project.speakers"
              :key="speaker.id"
              class="flex items-center gap-2 p-1.5 rounded-lg hover:bg-gray-700/50 group"
            >
              <!-- Color dot -->
              <button
                class="w-3 h-3 rounded-full flex-shrink-0 ring-1 ring-white/10 hover:ring-white/30 transition-all"
                :style="{ background: speaker.color ?? '#6b7280' }"
                :title="`编辑 ${speaker.label ?? speaker.speaker_id} 颜色`"
                @click="openSpeakerModal(speaker)"
              />
              <!-- Inline editable label -->
              <input
                v-model="speakerInlineLabels[speaker.speaker_id]"
                type="text"
                class="flex-1 min-w-0 bg-transparent text-xs text-gray-300 focus:outline-none focus:bg-gray-700 rounded px-1 py-0.5 truncate"
                :title="speaker.speaker_id"
                @blur="onSpeakerLabelBlur(speaker)"
                @keydown.enter.prevent="($event.target as HTMLInputElement).blur()"
              />
            </div>
          </div>
        </aside>

        <!-- CENTER: video + subtitle list -->
        <div class="flex-1 flex min-w-0 overflow-hidden">

          <!-- Video + subtitle (side by side when video exists on lg+) -->
          <template v-if="hasVideo">
            <!-- VIDEO COLUMN: overflow-y-auto so extra content below the player
                 (speakers on small screens) can scroll, while the player itself
                 stays sticky at the top of the column. -->
            <div class="w-80 lg:w-1/2 xl:w-2/5 flex-shrink-0 flex flex-col overflow-y-auto border-r border-gray-700">
              <!-- Sticky video wrapper — always visible even when column scrolls -->
              <div class="sticky top-0 z-10 bg-gray-900 p-3">
                <VideoPlayer
                  :project-id="projectId"
                  @timeupdate="onVideoTimeUpdate"
                />
              </div>

              <!-- Speakers panel on small screens (below video, non-sticky) -->
              <div
                v-if="project.speakers && project.speakers.length > 0"
                class="lg:hidden border-t border-gray-700 p-3 flex-shrink-0"
              >
                <h2 class="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">说话人</h2>
                <div class="flex flex-wrap gap-2">
                  <button
                    v-for="speaker in project.speakers"
                    :key="speaker.id"
                    class="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-gray-800 border border-gray-700 text-xs text-gray-300 hover:border-gray-600 transition-colors"
                    @click="openSpeakerModal(speaker)"
                  >
                    <span
                      class="w-2 h-2 rounded-full flex-shrink-0"
                      :style="{ background: speaker.color ?? '#6b7280' }"
                    />
                    {{ speaker.label ?? speaker.speaker_id }}
                  </button>
                </div>
              </div>
            </div>

            <!-- SUBTITLE COLUMN -->
            <div class="flex-1 flex flex-col min-w-0 overflow-hidden">
              <SubtitleEditor />
            </div>
          </template>

          <!-- No video: full-width subtitle list -->
          <template v-else>
            <div class="flex-1 flex flex-col min-w-0 overflow-hidden">
              <SubtitleEditor />
            </div>
          </template>

        </div>
      </template>
    </div>

    <!-- ======================================================================
         SPEAKER EDIT MODAL
    ====================================================================== -->
    <Teleport to="body">
      <Transition name="modal">
        <div
          v-if="speakerEditing"
          class="fixed inset-0 z-50 flex items-center justify-center p-4"
          @click.self="closeSpeakerModal"
        >
          <!-- Backdrop -->
          <div class="absolute inset-0 bg-black/60 backdrop-blur-sm" @click="closeSpeakerModal" />

          <!-- Modal card -->
          <div class="relative bg-gray-800 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-sm p-6 space-y-5">
            <div class="flex items-center justify-between">
              <h3 class="text-base font-semibold text-white">编辑说话人</h3>
              <button
                @click="closeSpeakerModal"
                class="text-gray-500 hover:text-white transition-colors"
              >
                <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
                </svg>
              </button>
            </div>

            <div class="space-y-4">
              <!-- Speaker ID (read-only) -->
              <div>
                <label class="block text-xs text-gray-500 mb-1">说话人 ID</label>
                <p class="text-sm text-gray-400 font-mono bg-gray-900 rounded-lg px-3 py-2">
                  {{ speakerEditing.speaker_id }}
                </p>
              </div>

              <!-- Label -->
              <div>
                <label class="block text-xs text-gray-400 mb-1.5">显示名称</label>
                <input
                  v-model="speakerEditLabel"
                  type="text"
                  class="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500 transition-colors"
                  placeholder="输入显示名称..."
                  @keydown.enter.prevent="saveSpeaker"
                />
              </div>

              <!-- Color -->
              <div>
                <label class="block text-xs text-gray-400 mb-1.5">标签颜色</label>
                <div class="flex items-center gap-3">
                  <input
                    v-model="speakerEditColor"
                    type="color"
                    class="w-10 h-10 rounded-lg overflow-hidden cursor-pointer bg-transparent border-0 p-0"
                  />
                  <span class="text-sm text-gray-400 font-mono">{{ speakerEditColor }}</span>
                </div>
              </div>
            </div>

            <!-- Actions -->
            <div class="flex gap-2 pt-1">
              <button
                @click="closeSpeakerModal"
                class="flex-1 py-2 bg-gray-700 hover:bg-gray-600 text-gray-300 text-sm rounded-lg transition-colors"
              >
                取消
              </button>
              <button
                @click="saveSpeaker"
                :disabled="speakerSaving"
                class="flex-1 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded-lg transition-colors flex items-center justify-center gap-2"
              >
                <svg v-if="speakerSaving" class="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 100 16v-4l-3 3 3 3v-4a8 8 0 01-8-8z"/>
                </svg>
                {{ speakerSaving ? '保存中...' : '保存' }}
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

  </div>
</template>

<style scoped>
.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.2s ease;
}
.modal-enter-active .relative,
.modal-leave-active .relative {
  transition: transform 0.2s ease, opacity 0.2s ease;
}
.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}
.modal-enter-from .relative,
.modal-leave-to .relative {
  transform: scale(0.95);
  opacity: 0;
}
</style>
