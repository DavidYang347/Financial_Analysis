<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type ScreenRecord } from '@/api'
import { errorText } from '@/api/http'
import { fmtDuration, fmtTime } from '@/utils/format'

const items = ref<ScreenRecord[]>([])
const loading = ref(false)
const error = ref('')

async function load() {
  loading.value = true
  try {
    items.value = (await api.screenerHistory(undefined, 200)).items
    error.value = ''
  } catch (e) {
    error.value = errorText(e)
  } finally {
    loading.value = false
  }
}

function paramText(p: Record<string, unknown>): string {
  return Object.entries(p).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join('/') : v}`).join('  ')
}

onMounted(load)
</script>

<template>
  <div class="page">
    <section class="sheet">
      <div class="sheet-head">
        <h2>筛选记录</h2>
        <span class="muted">每次运行的参数和结果都保存在本地，可以随时回看</span>
        <div class="spacer" />
        <el-button :loading="loading" @click="load">刷新</el-button>
      </div>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
      <el-table v-else v-loading="loading" :data="items">
        <el-table-column label="运行时间" width="170">
          <template #default="{ row }">{{ fmtTime(row.started_at) }}</template>
        </el-table-column>
        <el-table-column label="方法" min-width="140">
          <template #default="{ row }">
            <RouterLink :to="{ name: 'screening-method', params: { id: row.screener_id } }">{{ row.screener_name }}</RouterLink>
          </template>
        </el-table-column>
        <el-table-column prop="as_of" label="筛选日期" width="110" />
        <el-table-column label="结果" width="100" align="right">
          <template #default="{ row }">
            <span v-if="row.status === 'ok'">{{ row.count }} 只</span>
            <el-tooltip v-else :content="row.error" placement="top"><el-tag type="danger" size="small">出错</el-tag></el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="用时" width="90" align="right">
          <template #default="{ row }">{{ fmtDuration(row.elapsed_sec) }}</template>
        </el-table-column>
        <el-table-column label="参数" min-width="260">
          <template #default="{ row }"><span class="pv">{{ paramText(row.params) }}</span></template>
        </el-table-column>
        <el-table-column width="150" align="right">
          <template #default="{ row }">
            <RouterLink v-if="row.status === 'ok'" :to="{ name: 'screening-run', params: { runId: row.run_id } }">查看结果</RouterLink>
            <a v-if="row.status === 'ok'" class="csv" :href="api.screenCsvUrl(row.run_id)" download>CSV</a>
          </template>
        </el-table-column>
        <template #empty>
          <div class="empty"><strong>还没有筛选记录</strong>在“筛选方法”里选一个方法运行</div>
        </template>
      </el-table>
    </section>
  </div>
</template>

<style scoped>
.pv {
  color: var(--ink-2);
  font-size: var(--fs-xs);
  word-break: break-all;
}

.csv {
  margin-left: 14px;
}
</style>
