<script setup lang="ts">
import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps<{ source: string }>()
const html = computed(() => DOMPurify.sanitize(marked.parse(props.source ?? '', { async: false }) as string))
</script>

<template>
  <!-- eslint-disable-next-line vue/no-v-html -- sanitized with DOMPurify -->
  <div class="md" v-html="html" />
</template>

<style scoped>
.md {
  max-width: 68ch;
  color: var(--ink-2);
  line-height: 1.7;
}

.md :deep(p) {
  margin: 0 0 10px;
}

.md :deep(strong) {
  color: var(--ink);
  font-weight: 600;
}

.md :deep(ol),
.md :deep(ul) {
  margin: 0 0 10px;
  padding-left: 22px;
}

.md :deep(code) {
  padding: 1px 5px;
  background: #f0f3f7;
  border-radius: 3px;
  font-family: "IBM Plex Mono", ui-monospace, Menlo, monospace;
  font-size: 0.9em;
}

.md :deep(pre) {
  padding: 10px 12px;
  background: #f5f7fa;
  border: 1px solid var(--rule-soft);
  border-radius: 4px;
  overflow: auto;
  font-size: var(--fs-xs);
}

.md :deep(pre code) {
  padding: 0;
  background: none;
}
</style>
