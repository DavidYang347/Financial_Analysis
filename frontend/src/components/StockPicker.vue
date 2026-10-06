<script setup lang="ts">
import { ref } from 'vue'
import { api, type Stock } from '@/api'

const props = withDefaults(defineProps<{ modelValue: string; placeholder?: string; width?: string }>(), {
  placeholder: '代码或名称，如 600519 / 茅台',
  width: '280px',
})
const emit = defineEmits<{ 'update:modelValue': [string]; select: [Stock] }>()

const text = ref(props.modelValue)

interface Suggestion { value: string; stock: Stock }

async function query(q: string, cb: (items: Suggestion[]) => void) {
  const k = q.trim()
  if (!k) return cb([])
  try {
    const r = await api.searchStocks(k, 15)
    cb(r.items.map((s) => ({ value: `${s.symbol} ${s.name}`, stock: s })))
  } catch {
    cb([])
  }
}

function onSelect(item: Record<string, unknown>) {
  const s = (item as unknown as Suggestion).stock
  text.value = `${s.symbol} ${s.name}`
  emit('update:modelValue', s.symbol)
  emit('select', s)
}
</script>

<template>
  <el-autocomplete
    v-model="text"
    :fetch-suggestions="query"
    :placeholder="placeholder"
    :style="{ width }"
    :debounce="200"
    clearable
    highlight-first-item
    aria-label="搜索股票"
    @select="onSelect"
  >
    <template #default="{ item }">
      <div class="opt">
        <span class="sym">{{ item.stock.symbol }}</span>
        <span class="nm">{{ item.stock.name }}</span>
        <span class="bd">{{ item.stock.status === 'delisted' ? '已退市' : item.stock.board }}</span>
      </div>
    </template>
  </el-autocomplete>
</template>

<style scoped>
.opt {
  display: grid;
  grid-template-columns: 88px 1fr auto;
  gap: 8px;
  align-items: center;
}

.sym {
  font-variant-numeric: tabular-nums;
}

.bd {
  color: var(--muted);
  font-size: var(--fs-xs);
}
</style>
