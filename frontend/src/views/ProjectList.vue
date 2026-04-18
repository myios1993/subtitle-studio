<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import type { ProjectListItem } from '../api/types'
import { projectsApi } from '../api/projects'

const router = useRouter()
const projects = ref<ProjectListItem[]>([])
const loading = ref(true)
const showCreateDialog = ref(false)

// Create form
const newName = ref('')
const newMode = ref<'file' | 'microphone' | 'loopback'>('file')

onMounted(async () => {
  await loadProjects()
})

async function loadProjects() {
  loading.value = true
  try {
    projects.value = await projectsApi.list()
  } finally {
    loading.value = false
  }
}

async function createProject() {
  if (!newName.value.trim()) return
  const p = await projectsApi.create({ name: newName.value.trim(), capture_mode: newMode.value })
  showCreateDialog.value = false
  newName.value = ''
  router.push(`/project/${p.id}`)
}

async function deleteProject(id: number, e: Event) {
  e.stopPropagation()
  if (!confirm('确定删除此项目？所有字幕数据将丢失。')) return
  await projectsApi.delete(id)
  await loadProjects()
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

const statusLabels: Record<string, string> = {
  idle: '待处理', capturing: '录制中', processing: '处理中', done: '已完成', error: '出错',
}
const statusColors: Record<string, string> = {
  idle: 'bg-gray-600', capturing: 'bg-yellow-600', processing: 'bg-blue-600 animate-pulse', done: 'bg-green-600', error: 'bg-red-600',
}
const modeLabels: Record<string, string> = {
  file: '文件导入', microphone: '麦克风', loopback: '系统声音',
}
</script>

<template>
  <div class="max-w-4xl mx-auto p-6">
    <!-- Header -->
    <div class="flex items-center justify-between mb-8">
      <div>
        <h1 class="text-2xl font-bold text-white">项目列表</h1>
        <p class="text-gray-400 text-sm mt-1">创建项目以开始自动生成字幕</p>
      </div>
      <button
        @click="showCreateDialog = true"
        class="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors flex items-center gap-2"
      >
        <span class="text-xl leading-none">+</span>
        新建项目
      </button>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="text-center py-20 text-gray-500">加载中...</div>

    <!-- Empty state -->
    <div v-else-if="projects.length === 0" class="text-center py-20">
      <div class="text-6xl mb-4">🎬</div>
      <p class="text-gray-400">还没有项目，点击「新建项目」开始</p>
    </div>

    <!-- Project cards -->
    <div v-else class="grid gap-3">
      <div
        v-for="p in projects"
        :key="p.id"
        @click="router.push(`/project/${p.id}`)"
        class="bg-gray-900 border border-gray-800 rounded-lg p-4 hover:border-gray-600 cursor-pointer transition-colors group"
      >
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-3 min-w-0">
            <h3 class="text-white font-medium truncate">{{ p.name }}</h3>
            <span :class="statusColors[p.status]" class="text-xs px-2 py-0.5 rounded-full text-white whitespace-nowrap">
              {{ statusLabels[p.status] || p.status }}
            </span>
          </div>
          <button
            @click="deleteProject(p.id, $event)"
            class="text-gray-600 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-all text-lg"
            title="删除项目"
          >
            ✕
          </button>
        </div>
        <div class="flex items-center gap-4 mt-2 text-sm text-gray-500">
          <span>{{ modeLabels[p.capture_mode] || p.capture_mode }}</span>
          <span>{{ p.segment_count }} 条字幕</span>
          <span>{{ formatDate(p.created_at) }}</span>
        </div>
      </div>
    </div>

    <!-- Create dialog -->
    <Teleport to="body">
      <div v-if="showCreateDialog" class="fixed inset-0 z-50 flex items-center justify-center bg-black/60" @click.self="showCreateDialog = false">
        <div class="bg-gray-900 border border-gray-700 rounded-xl p-6 w-full max-w-md">
          <h2 class="text-lg font-semibold text-white mb-4">新建项目</h2>

          <label class="block text-sm text-gray-400 mb-1">项目名称</label>
          <input
            v-model="newName"
            @keyup.enter="createProject"
            class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-blue-500 mb-4"
            placeholder="例如：会议录音 2026-04-16"
            autofocus
          />

          <label class="block text-sm text-gray-400 mb-2">音频来源</label>
          <div class="grid grid-cols-3 gap-2 mb-6">
            <button
              v-for="m in (['file', 'microphone', 'loopback'] as const)"
              :key="m"
              @click="newMode = m"
              :class="newMode === m ? 'border-blue-500 bg-blue-500/10 text-blue-400' : 'border-gray-700 text-gray-400 hover:border-gray-500'"
              class="border rounded-lg py-2 px-3 text-sm transition-colors text-center"
            >
              {{ modeLabels[m] }}
            </button>
          </div>

          <div class="flex justify-end gap-3">
            <button @click="showCreateDialog = false" class="px-4 py-2 text-gray-400 hover:text-white transition-colors">取消</button>
            <button
              @click="createProject"
              :disabled="!newName.trim()"
              class="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-lg transition-colors"
            >
              创建
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>
