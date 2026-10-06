<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { modules } from './modules'
import { dataStatus, refreshDataStatus } from './stores/dataStatus'
import { fmtInt } from './utils/format'

const route = useRoute()
const active = computed(() => modules.find((m) => route.path.startsWith(m.path)))
const subnav = computed(() =>
  (active.value?.routes[0]?.children ?? []).filter((r) => r.meta?.title && !r.meta?.hidden),
)

const freshness = computed(() => {
  const s = dataStatus.value
  if (!s) return null
  return { last: s.last_date, symbols: s.latest_day_symbols ?? s.symbols, running: s.job?.status === 'running' }
})

let timer: number | undefined
onMounted(() => {
  refreshDataStatus()
  timer = window.setInterval(refreshDataStatus, 30_000)
})
onUnmounted(() => window.clearInterval(timer))
</script>

<template>
  <div class="shell">
    <aside class="rail" aria-label="模块">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true">
          <i class="c up" /><i class="c down" />
        </span>
        <span class="brand-name">A股研究台</span>
      </div>
      <nav>
        <RouterLink
          v-for="m in modules"
          :key="m.id"
          :to="m.path"
          class="rail-item"
          :class="{ on: active?.id === m.id }"
        >
          <el-icon :size="18"><component :is="m.icon" /></el-icon>
          <span>{{ m.title }}</span>
        </RouterLink>
      </nav>
      <p class="rail-foot">新模块放在 frontend/src/modules/ 下即可出现在这里</p>
    </aside>

    <div class="main">
      <header class="topbar">
        <nav v-if="subnav.length" class="tabs" aria-label="子页面">
          <RouterLink
            v-for="r in subnav"
            :key="String(r.name)"
            :to="{ name: r.name as string }"
            class="tab"
            active-class="on"
          >
            {{ r.meta?.title }}
          </RouterLink>
        </nav>
        <div class="spacer" />
        <div class="fresh" :title="dataStatus.value?.lake_dir">
          <template v-if="dataStatus.error">
            <span class="dot bad" />后端未连接
          </template>
          <template v-else-if="freshness">
            <span class="dot" :class="freshness.running ? 'busy' : 'ok'" />
            <span v-if="freshness.running">数据更新中</span>
            <span v-else>行情截至 <b>{{ freshness.last ?? '–' }}</b></span>
            <span class="muted">{{ fmtInt(freshness.symbols) }} 只</span>
          </template>
        </div>
      </header>
      <RouterView />
    </div>
  </div>
</template>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: 184px 1fr;
  min-height: 100%;
}

.rail {
  position: sticky;
  top: 0;
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #1f3557;
  color: #c9d3e1;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 56px;
  padding: 0 18px;
  color: #fff;
}

.brand-name {
  font-size: var(--fs-lg);
  font-weight: 600;
  letter-spacing: 0.02em;
}

/* Two candles: the one mark the product needs. */
.brand-mark {
  display: inline-flex;
  align-items: flex-end;
  gap: 3px;
  height: 20px;
}

.brand-mark .c {
  display: block;
  width: 6px;
  border-radius: 1px;
}

.brand-mark .c.up {
  height: 14px;
  background: var(--up);
}

.brand-mark .c.down {
  height: 20px;
  background: var(--down);
}

nav {
  display: flex;
  flex-direction: column;
  padding: 8px 0;
}

.rail-item {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 42px;
  padding: 0 18px;
  color: inherit;
  text-decoration: none;
  border-left: 3px solid transparent;
}

.rail-item:hover {
  background: rgba(255, 255, 255, 0.06);
  color: #fff;
}

.rail-item.on {
  background: rgba(255, 255, 255, 0.1);
  border-left-color: #fff;
  color: #fff;
  font-weight: 500;
}

.rail-foot {
  margin: auto 0 0;
  padding: 16px 18px;
  font-size: var(--fs-xs);
  line-height: 1.5;
  color: #8a9ab2;
}

.main {
  min-width: 0;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: stretch;
  height: 48px;
  padding: 0 24px;
  background: var(--surface);
  border-bottom: 1px solid var(--rule);
}

.tabs {
  display: flex;
  flex-direction: row;
  gap: 4px;
  padding: 0;
}

.tab {
  display: flex;
  align-items: center;
  padding: 0 14px;
  color: var(--ink-2);
  text-decoration: none;
  border-bottom: 2px solid transparent;
}

.tab:hover {
  color: var(--ink);
}

.tab.on {
  color: var(--accent);
  border-bottom-color: var(--accent);
  font-weight: 500;
}

.fresh {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--fs-sm);
  color: var(--ink-2);
}

.fresh b {
  font-weight: 600;
  color: var(--ink);
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--muted);
}

.dot.ok {
  background: var(--down);
}

.dot.busy {
  background: #d98e1c;
}

.dot.bad {
  background: var(--up);
}

@media (max-width: 860px) {
  .shell {
    grid-template-columns: 1fr;
  }

  .rail {
    position: static;
    height: auto;
    flex-direction: row;
    align-items: center;
  }

  .rail nav {
    flex-direction: row;
    padding: 0;
  }

  .rail-item {
    border-left: 0;
    border-bottom: 3px solid transparent;
  }

  .rail-item.on {
    border-bottom-color: #fff;
  }

  .rail-foot {
    display: none;
  }

  .topbar {
    padding: 0 12px;
    overflow-x: auto;
  }

  .fresh {
    display: none;
  }
}
</style>
