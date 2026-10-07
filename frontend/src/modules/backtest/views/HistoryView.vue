<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { strategyApi, type BacktestRecord } from '@/api'
import { errorText } from '@/api/http'
import { fmtDuration, fmtTime } from '@/utils/format'
import { num, paramText, pct, settingsText, sign } from '../labels'

const router = useRouter()
const items = ref<BacktestRecord[]>([])
const loading = ref(false)
const error = ref('')
const strategy = ref('')
const selected = ref<BacktestRecord[]>([])

const strategies = computed(() => {
  const m = new Map<string, string>()
  for (const r of items.value) m.set(r.strategy_id, r.strategy_name)
  return [...m.entries()].map(([id, name]) => ({ id, name }))
})
const shown = computed(() => (strategy.value ? items.value.filter((r) => r.strategy_id === strategy.value) : items.value))

async function load() {
  loading.value = true
  try {
    items.value = (await strategyApi.history(undefined, 500)).items
    error.value = ''
  } catch (e) {
    error.value = errorText(e)
  } finally {
    loading.value = false
  }
}

function compare() {
  if (selected.value.length < 2) {
    ElMessage.warning('勾选至少两条成功的回测')
    return
  }
  if (selected.value.length > 6) {
    ElMessage.warning('最多同时对比 6 条')
    return
  }
  router.push({ name: 'backtest-compare', query: { ids: selected.value.map((r) => r.run_id).join(',') } })
}

function open(row: BacktestRecord) {
  if (row.status === 'ok') router.push({ name: 'backtest-report', params: { runId: row.run_id } })
}

onMounted(load)
</script>

<template>
  <div class="page">
    <section class="sheet">
      <div class="sheet-head">
        <h2>回测记录</h2>
        <span class="muted">每次回测的参数、设置和完整结果都保存在本地</span>
        <div class="spacer" />
        <el-select v-model="strategy" placeholder="全部策略" clearable size="default" class="fsel" aria-label="按策略过滤">
          <el-option v-for="s in strategies" :key="s.id" :value="s.id" :label="s.name" />
        </el-select>
        <el-button :disabled="selected.length < 2" @click="compare">对比所选（{{ selected.length }}）</el-button>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </div>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
      <el-table
        v-else
        v-loading="loading"
        :data="shown"
        row-key="run_id"
        class="htable"
        @selection-change="selected = $event"
        @row-dblclick="open"
      >
        <el-table-column type="selection" width="44" :selectable="(row: BacktestRecord) => row.status === 'ok'" />
        <el-table-column label="运行时间" width="160">
          <template #default="{ row }">{{ fmtTime(row.started_at) }}</template>
        </el-table-column>
        <el-table-column label="策略" min-width="130">
          <template #default="{ row }">
            <RouterLink :to="{ name: 'strategy', params: { id: row.strategy_id } }">{{ row.strategy_name }}</RouterLink>
          </template>
        </el-table-column>
        <el-table-column label="设置 / 参数" min-width="300">
          <template #default="{ row }">
            <div class="pv">{{ settingsText(row.settings) }}</div>
            <div class="pv muted">{{ paramText(row.params) }}</div>
          </template>
        </el-table-column>
        <el-table-column label="总收益" width="100" align="right">
          <template #default="{ row }">
            <span v-if="row.status === 'ok'" :class="sign(row.metrics?.total_return)">{{ pct(row.metrics?.total_return) }}</span>
            <el-tooltip v-else :content="row.error" placement="top">
              <el-tag :type="row.status === 'cancelled' ? 'info' : 'danger'" size="small">
                {{ row.status === 'cancelled' ? '已取消' : '出错' }}
              </el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="年化" width="90" align="right">
          <template #default="{ row }"><span :class="sign(row.metrics?.annual_return)">{{ pct(row.metrics?.annual_return) }}</span></template>
        </el-table-column>
        <el-table-column label="超额" width="90" align="right">
          <template #default="{ row }"><span :class="sign(row.metrics?.excess_return)">{{ pct(row.metrics?.excess_return) }}</span></template>
        </el-table-column>
        <el-table-column label="最大回撤" width="90" align="right">
          <template #default="{ row }">{{ pct(row.metrics?.max_drawdown, 2, false) }}</template>
        </el-table-column>
        <el-table-column label="夏普" width="70" align="right">
          <template #default="{ row }">{{ num(row.metrics?.sharpe) }}</template>
        </el-table-column>
        <el-table-column label="用时" width="80" align="right">
          <template #default="{ row }">{{ fmtDuration(row.elapsed_sec) }}</template>
        </el-table-column>
        <el-table-column width="90" align="right">
          <template #default="{ row }">
            <RouterLink v-if="row.status === 'ok'" :to="{ name: 'backtest-report', params: { runId: row.run_id } }">查看报告</RouterLink>
          </template>
        </el-table-column>
        <template #empty>
          <div class="empty"><strong>还没有回测记录</strong>在“新建回测”里选一个策略运行</div>
        </template>
      </el-table>
    </section>
  </div>
</template>

<style scoped>
.fsel {
  width: 180px;
}

.pv {
  font-size: var(--fs-xs);
  line-height: 1.5;
  word-break: break-all;
}

.htable :deep(.el-table__row) {
  cursor: default;
}
</style>
