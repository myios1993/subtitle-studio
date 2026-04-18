<script setup lang="ts">
import { ref } from 'vue'
import { useProjectStore } from '../stores/project'

const store = useProjectStore()
const showOptions = ref(false)
const textMode = ref<'original' | 'translated' | 'bilingual'>('bilingual')
const includeSpeaker = ref(true)

function exportSrt() {
  if (!store.project) return
  const params = new URLSearchParams({
    text_mode: textMode.value,
    include_speaker: String(includeSpeaker.value),
  })
  // Trigger browser download
  window.open(`/api/export/${store.project.id}/srt?${params}`, '_blank')
  showOptions.value = false
}
</script>

<template>
  <div class="relative">
    <button
      @click="showOptions = !showOptions"
      :disabled="!store.segments.length"
      class="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 disabled:opacity-30 disabled:cursor-not-allowed text-white text-sm rounded-lg transition-colors"
    >
      导出 SRT
    </button>

    <!-- Options dropdown -->
    <div
      v-if="showOptions"
      class="absolute right-0 top-full mt-1 bg-gray-800 border border-gray-700 rounded-lg p-3 w-56 z-10 space-y-3"
    >
      <div>
        <label class="text-xs text-gray-400 block mb-1">文字内容</label>
        <select v-model="textMode" class="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1 text-sm text-gray-300">
          <option value="original">仅原文</option>
          <option value="translated">仅译文</option>
          <option value="bilingual">双语</option>
        </select>
      </div>

      <label class="flex items-center gap-2 text-sm text-gray-300">
        <input type="checkbox" v-model="includeSpeaker" class="rounded" />
        显示说话人
      </label>

      <button
        @click="exportSrt"
        class="w-full py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition-colors"
      >
        下载 .srt 文件
      </button>
    </div>
  </div>
</template>
