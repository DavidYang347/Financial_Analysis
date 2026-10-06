<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, type ScreenResult } from '@/api'
import { errorText } from '@/api/http'
import ResultTable from '@/components/ResultTable.vue'
import { fmtDuration, fmtTime } from '@/utils/format'

const route = useRoute()
const r = ref<ScreenResult | null>(null)
const error = ref('')

onMounted(async () => {
  try {
    r.value = await api.screenRun(String(route.params.runId))
  } catch (e) {
    error.value = errorText(e)
  }
})
</script>

<template>
  <div class="page">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <section v-else-if="r" class="sheet">
      <div class="sheet-head">
        <RouterLink :to="{ name: 'screening-history' }">筛选记录</RouterLink>
        <span class="muted">/</span>
        <h2>{{ r.screener_name }}</h2>
        <span class="muted">{{ fmtTime(r.started_at) }}</span>
        <div class="spacer" />
        <RouterLink :to="{ name: 'screening-method', params: { id: r.screener_id } }">打开这个方法</RouterLink>
        <el-button tag="a" :href="api.screenCsvUrl(r.run_id)" download>导出 CSV</el-button>
      </div>
      <div class="sheet-body">
        <p class="summary">
          <span class="count">{{ r.count ?? 0 }}</span> 只股票，筛选日期 {{ r.as_of }}，用时 {{ fmtDuration(r.elapsed_sec) }}
        </p>
        <p class="pv">
          <span v-for="(v, k) in r.params" :key="k">{{ k }} = {{ Array.isArray(v) ? v.join('、') : v }}</span>
        </p>
        <ResultTable :columns="r.columns" :items="r.items" />
      </div>
    </section>
  </div>
</template>

<style scoped>
.summary {
  margin: 0 0 6px;
}

.count {
  font-size: var(--fs-2xl);
  font-weight: 600;
  color: var(--accent);
}

.pv {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  margin: 0 0 14px;
  color: var(--ink-2);
  font-size: var(--fs-sm);
}
</style>
