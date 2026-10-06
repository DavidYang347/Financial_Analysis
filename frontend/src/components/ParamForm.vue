<script setup lang="ts">
import { computed } from 'vue'
import type { ParamDef } from '@/api'

/** Form generated from a screener's PARAMS declaration. */
const props = defineProps<{ params: ParamDef[]; modelValue: Record<string, unknown>; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [Record<string, unknown>] }>()

const groups = computed(() => {
  const out: { name: string; items: ParamDef[] }[] = []
  for (const p of props.params) {
    const name = p.group || '参数'
    let g = out.find((x) => x.name === name)
    if (!g) out.push((g = { name, items: [] }))
    g.items.push(p)
  }
  // Method-specific parameters first, shared stock-pool filters after.
  return out.sort((a, b) => Number(a.name !== '参数') - Number(b.name !== '参数'))
})

function set(key: string, v: unknown) {
  emit('update:modelValue', { ...props.modelValue, [key]: v })
}

function isChanged(p: ParamDef): boolean {
  return JSON.stringify(props.modelValue[p.key]) !== JSON.stringify(p.default)
}
</script>

<template>
  <el-form label-position="top" :disabled="disabled" class="pform" @submit.prevent>
    <fieldset v-for="g in groups" :key="g.name">
      <legend>{{ g.name }}</legend>
      <el-form-item v-for="p in g.items" :key="p.key" :class="{ changed: isChanged(p) }">
        <template #label>
          <span>{{ p.label }}</span>
          <el-tooltip v-if="p.help" :content="p.help" placement="top">
            <span class="q" tabindex="0" :aria-label="p.help">?</span>
          </el-tooltip>
        </template>

        <div class="field">
          <el-input-number
            v-if="p.type === 'int' || p.type === 'float'"
            :model-value="modelValue[p.key] as number"
            :min="p.min ?? undefined"
            :max="p.max ?? undefined"
            :step="p.step ?? (p.type === 'int' ? 1 : 0.1)"
            :precision="p.type === 'int' ? 0 : undefined"
            controls-position="right"
            @update:model-value="set(p.key, $event)"
          />
          <el-switch
            v-else-if="p.type === 'bool'"
            :model-value="Boolean(modelValue[p.key])"
            @update:model-value="set(p.key, $event)"
          />
          <el-select
            v-else-if="p.type === 'select'"
            :model-value="modelValue[p.key]"
            @update:model-value="set(p.key, $event)"
          >
            <el-option v-for="o in p.options ?? []" :key="String(o.value)" :value="o.value" :label="o.label" />
          </el-select>
          <el-checkbox-group
            v-else-if="p.type === 'multiselect'"
            :model-value="(modelValue[p.key] as (string | number)[]) ?? []"
            @update:model-value="set(p.key, $event)"
          >
            <el-checkbox v-for="o in p.options ?? []" :key="String(o.value)" :value="o.value">{{ o.label }}</el-checkbox>
          </el-checkbox-group>
          <el-date-picker
            v-else-if="p.type === 'date'"
            :model-value="modelValue[p.key] as string"
            type="date"
            value-format="YYYY-MM-DD"
            @update:model-value="set(p.key, $event)"
          />
          <el-input v-else :model-value="String(modelValue[p.key] ?? '')" @update:model-value="set(p.key, $event)" />
          <span v-if="p.unit" class="unit">{{ p.unit }}</span>
        </div>
      </el-form-item>
    </fieldset>
  </el-form>
</template>

<style scoped>
.pform fieldset {
  margin: 0 0 8px;
  padding: 0;
  border: 0;
}

.pform legend {
  width: 100%;
  margin-bottom: 8px;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--rule-soft);
  color: var(--ink-2);
  font-size: var(--fs-sm);
  font-weight: 600;
}

.pform :deep(.el-form-item) {
  margin-bottom: 12px;
}

.pform :deep(.el-form-item__label) {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
  line-height: 1.4;
}

/* A changed value is marked by a bar on the left: the form shows at a
   glance which settings differ from the script's defaults. */
.changed {
  box-shadow: inset 2px 0 0 var(--accent);
  padding-left: 8px;
  margin-left: -10px;
}

.field {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.unit {
  color: var(--muted);
  font-size: var(--fs-sm);
}

.q {
  display: inline-grid;
  place-items: center;
  width: 16px;
  height: 16px;
  border: 1px solid var(--rule);
  border-radius: 50%;
  color: var(--muted);
  font-size: 11px;
  cursor: help;
}
</style>
