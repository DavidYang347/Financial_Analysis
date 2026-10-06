<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '@/api'
import { errorText } from '@/api/http'

/** Shows a maintenance run's log; keeps tailing while the run is in progress. */
const props = defineProps<{ runId: string | null | undefined }>()

const text = ref('')
const error = ref('')
const running = ref(false)
const box = ref<HTMLPreElement>()
let offset = 0
let timer: number | undefined
let follow = true

function onScroll() {
  const el = box.value
  if (el) follow = el.scrollTop + el.clientHeight >= el.scrollHeight - 24
}

async function poll() {
  if (!props.runId) return
  try {
    const r = await api.runLog(props.runId, offset)
    if (r.text) {
      text.value += r.text
      offset = r.next_offset
      if (follow) {
        await nextTick()
        if (box.value) box.value.scrollTop = box.value.scrollHeight
      }
    }
    running.value = r.running
    error.value = ''
  } catch (e) {
    error.value = errorText(e)
    running.value = false
  }
  window.clearTimeout(timer)
  if (running.value) timer = window.setTimeout(poll, 1500)
}

watch(
  () => props.runId,
  () => {
    window.clearTimeout(timer)
    text.value = ''
    offset = 0
    follow = true
    poll()
  },
  { immediate: true },
)
onBeforeUnmount(() => window.clearTimeout(timer))
defineExpose({ refresh: poll })
</script>

<template>
  <div>
    <el-alert v-if="error" :title="error" type="warning" :closable="false" show-icon />
    <pre v-else-if="text" ref="box" class="log" tabindex="0" aria-label="运行日志" @scroll="onScroll">{{ text }}</pre>
    <p v-else class="muted none">这次运行没有日志文件。2026-10-06 之前通过命令行跑的任务不会记录日志。</p>
    <p v-if="running" class="muted tail">正在运行，日志自动刷新</p>
  </div>
</template>

<style scoped>
.none,
.tail {
  margin: 8px 0 0;
  font-size: var(--fs-sm);
}
</style>
