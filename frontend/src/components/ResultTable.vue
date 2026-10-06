<script setup lang="ts">
import { computed, ref } from 'vue'
import { api, type Bar } from '@/api'
import KlineChart from '@/components/KlineChart.vue'
import { fmtNum } from '@/utils/format'

/** Screening result table. Click a row to preview its K-line below the table. */
const props = defineProps<{ columns: { key: string; label: string }[]; items: Record<string, unknown>[] }>()

const preview = ref<{ symbol: string; name: string; bars: Bar[] } | null>(null)
const loading = ref(false)

const numericKeys = computed(() =>
  new Set(props.columns.filter((c) => props.items.some((r) => typeof r[c.key] === 'number')).map((c) => c.key)),
)

function cell(row: Record<string, unknown>, key: string): string {
  const v = row[key]
  if (v === null || v === undefined) return '–'
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : fmtNum(v, 2)
  if (Array.isArray(v)) return v.join(', ')
  return String(v)
}

/** Columns whose name says they are a change get red/green. */
function signClass(key: string, row: Record<string, unknown>): string {
  if (!/pct|ret|chg|涨/.test(key)) return ''
  const n = Number(row[key])
  return n > 0 ? 'up' : n < 0 ? 'down' : ''
}

async function open(row: Record<string, unknown>) {
  const symbol = String(row.symbol)
  if (preview.value?.symbol === symbol) return
  loading.value = true
  try {
    const start = new Date()
    start.setFullYear(start.getFullYear() - 1)
    const r = await api.daily(symbol, 'qfq', start.toISOString().slice(0, 10))
    preview.value = { symbol, name: String(row.name ?? ''), bars: r.items }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div>
    <el-table
      :data="items"
      max-height="520"
      highlight-current-row
      :default-sort="undefined"
      @row-click="open"
    >
      <el-table-column type="index" label="#" width="56" />
      <el-table-column
        v-for="c in columns"
        :key="c.key"
        :prop="c.key"
        :label="c.label"
        :align="numericKeys.has(c.key) ? 'right' : 'left'"
        :min-width="c.key === 'name' ? 110 : 96"
        :fixed="c.key === 'symbol' ? 'left' : undefined"
        sortable
      >
        <template #default="{ row }">
          <RouterLink
            v-if="c.key === 'symbol'"
            :to="{ name: 'data-kline', query: { symbol: row.symbol } }"
            @click.stop
          >{{ row.symbol }}</RouterLink>
          <span v-else :class="signClass(c.key, row)">{{ cell(row, c.key) }}</span>
        </template>
      </el-table-column>
      <template #empty>
        <div class="empty"><strong>没有股票满足条件</strong>放宽参数后再运行一次</div>
      </template>
    </el-table>

    <div v-if="items.length" v-loading="loading" class="preview">
      <template v-if="preview">
        <div class="pv-head">
          <strong>{{ preview.name }}</strong>
          <span class="muted">{{ preview.symbol }}，近一年前复权</span>
          <div class="spacer" />
          <RouterLink :to="{ name: 'data-kline', query: { symbol: preview.symbol } }">在 K线预览中打开</RouterLink>
        </div>
        <KlineChart :bars="preview.bars" height="360px" :initial-bars="250" />
      </template>
      <p v-else class="muted hint">点击表格中的一行，在这里查看它的 K线</p>
    </div>
  </div>
</template>

<style scoped>
:deep(.el-table__row) {
  cursor: pointer;
}

.preview {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--rule-soft);
  min-height: 60px;
}

.pv-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 4px;
}

.hint {
  margin: 0;
  font-size: var(--fs-sm);
}
</style>
