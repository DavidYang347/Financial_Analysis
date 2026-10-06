<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, type RunSummary } from '@/api'
import { errorText } from '@/api/http'
import RunLog from '@/components/RunLog.vue'
import { RUN_MODE_LABEL, SOURCE_LABEL, fmtDuration, fmtInt, fmtTime } from '@/utils/format'

const route = useRoute()
const router = useRouter()

const runs = ref<RunSummary[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const loading = ref(false)
const error = ref('')
const selected = ref<RunSummary | null>(null)

async function load() {
  loading.value = true
  try {
    const r = await api.runs(pageSize, (page.value - 1) * pageSize)
    runs.value = r.items
    total.value = r.total
    error.value = ''
    const want = route.query.run
    if (want) selected.value = runs.value.find((x) => x.run_id === want) ?? (await api.run(String(want)))
    else if (!selected.value && runs.value.length) selected.value = runs.value[0]
  } catch (e) {
    error.value = errorText(e)
  } finally {
    loading.value = false
  }
}

function pick(r: RunSummary) {
  selected.value = r
  router.replace({ query: { run: r.run_id } })
}

function statusOf(r: RunSummary): { text: string; type: 'success' | 'warning' | 'danger' | 'info' } {
  if (r.error) return { text: '失败', type: 'danger' }
  if (!r.finished_at) return { text: '运行中', type: 'info' }
  if (r.complete === false) return { text: '未完成', type: 'warning' }
  if (r.symbols_failed) return { text: `${r.symbols_failed} 只失败`, type: 'warning' }
  return { text: '成功', type: 'success' }
}

watch(page, load)
onMounted(load)
</script>

<template>
  <div class="page">
    <div class="split">
      <section class="sheet">
        <div class="sheet-head">
          <h2>更新历史</h2>
          <span class="muted">共 {{ total }} 次</span>
          <div class="spacer" />
          <el-button :loading="loading" @click="load">刷新</el-button>
        </div>
        <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
        <el-table
          v-else
          v-loading="loading"
          :data="runs"
          highlight-current-row
          :current-row-key="selected?.run_id"
          row-key="run_id"
          @row-click="pick"
        >
          <el-table-column label="开始时间" width="170">
            <template #default="{ row }">{{ fmtTime(row.started_at) }}</template>
          </el-table-column>
          <el-table-column label="类型" width="90">
            <template #default="{ row }">{{ RUN_MODE_LABEL[row.mode] ?? row.mode }}</template>
          </el-table-column>
          <el-table-column label="结果" width="100">
            <template #default="{ row }">
              <el-tag :type="statusOf(row).type" size="small">{{ statusOf(row).text }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="更新至" prop="target_date" width="110" />
          <el-table-column label="股票" align="right" width="80">
            <template #default="{ row }">{{ fmtInt(row.symbols_updated) }}</template>
          </el-table-column>
          <el-table-column label="写入行数" align="right">
            <template #default="{ row }">{{ fmtInt(row.rows_written) }}</template>
          </el-table-column>
          <template #empty>
            <div class="empty"><strong>还没有更新记录</strong>到“数据更新”页开始第一次更新</div>
          </template>
        </el-table>
        <el-pagination
          v-if="total > pageSize"
          v-model:current-page="page"
          class="pager"
          layout="prev, pager, next"
          :page-size="pageSize"
          :total="total"
        />
      </section>

      <section class="sheet detail">
        <template v-if="selected">
          <div class="sheet-head">
            <h2>{{ RUN_MODE_LABEL[selected.mode] ?? selected.mode }}</h2>
            <span class="muted">{{ fmtTime(selected.started_at) }}</span>
          </div>
          <div class="sheet-body">
            <el-alert v-if="selected.error" :title="selected.error" type="error" :closable="false" show-icon />
            <dl class="facts">
              <div><dt>运行编号</dt><dd>{{ selected.run_id }}</dd></div>
              <div><dt>更新至交易日</dt><dd>{{ selected.target_date ?? '–' }}</dd></div>
              <div><dt>结束时间</dt><dd>{{ fmtTime(selected.finished_at) }}</dd></div>
              <div><dt>耗时</dt><dd>{{ fmtDuration(selected.elapsed_sec) }}</dd></div>
              <div><dt>需要更新</dt><dd>{{ fmtInt(selected.symbols_total) }} 只</dd></div>
              <div><dt>已更新</dt><dd>{{ fmtInt(selected.symbols_updated) }} 只</dd></div>
              <div><dt>无新数据</dt><dd>{{ fmtInt(selected.symbols_empty) }} 只</dd></div>
              <div><dt>失败</dt><dd :class="{ up: selected.symbols_failed }">{{ fmtInt(selected.symbols_failed) }} 只</dd></div>
              <div><dt>写入行数</dt><dd>{{ fmtInt(selected.rows_written) }}</dd></div>
              <div><dt>重算复权因子</dt><dd>{{ fmtInt(selected.factors_recomputed) }} 只</dd></div>
              <div>
                <dt>数据来自</dt>
                <dd>
                  <span v-for="(n, s) in selected.source_usage" :key="s" class="src">{{ SOURCE_LABEL[s] ?? s }} {{ fmtInt(n) }}</span>
                  <span v-if="!Object.keys(selected.source_usage ?? {}).length">–</span>
                </dd>
              </div>
              <div v-if="Object.keys(selected.disabled_sources ?? {}).length">
                <dt>跳过的数据源</dt>
                <dd>
                  <div v-for="(why, s) in selected.disabled_sources" :key="s" class="skip">
                    {{ SOURCE_LABEL[s] ?? s }}<span class="muted">：{{ why }}</span>
                  </div>
                </dd>
              </div>
            </dl>

            <template v-if="selected.failed?.length">
              <h3 class="sub">失败的股票</h3>
              <el-table :data="selected.failed" size="small" max-height="220">
                <el-table-column prop="symbol" label="代码" width="110" />
                <el-table-column label="各数据源返回">
                  <template #default="{ row }">
                    <div v-for="(msg, s) in row.errors" :key="s" class="err">{{ SOURCE_LABEL[s] ?? s }}：{{ msg }}</div>
                  </template>
                </el-table-column>
              </el-table>
            </template>

            <h3 class="sub">运行日志</h3>
            <RunLog :run-id="selected.has_log ? selected.run_id : null" />
          </div>
        </template>
        <div v-else class="empty"><strong>选择左侧一次更新</strong>查看它的统计和日志</div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.split {
  display: grid;
  grid-template-columns: minmax(0, 5fr) minmax(0, 6fr);
  gap: 16px;
  align-items: start;
}

.detail {
  position: sticky;
  top: 64px;
}

.pager {
  padding: 10px 12px;
  justify-content: flex-end;
}

:deep(.el-table__row) {
  cursor: pointer;
}

.facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 24px;
  margin: 0;
}

.facts div {
  display: flex;
  gap: 8px;
  align-items: baseline;
  min-width: 0;
}

.facts dt {
  flex: 0 0 auto;
  color: var(--muted);
  font-size: var(--fs-sm);
}

.facts dd {
  margin: 0;
  font-weight: 500;
  overflow-wrap: anywhere;
}

.src {
  margin-right: 10px;
}

.skip,
.err {
  font-weight: 400;
  font-size: var(--fs-sm);
}

.sub {
  margin: 20px 0 8px;
  font-size: var(--fs-md);
}

@media (max-width: 1100px) {
  .split {
    grid-template-columns: 1fr;
  }

  .detail {
    position: static;
  }

  .facts {
    grid-template-columns: 1fr;
  }
}
</style>
