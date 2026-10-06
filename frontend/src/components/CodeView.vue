<script setup lang="ts">
import { computed } from 'vue'
import hljs from 'highlight.js/lib/core'
import python from 'highlight.js/lib/languages/python'
import 'highlight.js/styles/github.css'

hljs.registerLanguage('python', python)

const props = defineProps<{ source: string; filename?: string }>()
const lines = computed(() => hljs.highlight(props.source ?? '', { language: 'python' }).value.split('\n'))
</script>

<template>
  <div class="code" role="region" :aria-label="filename ? `源码 ${filename}` : '源码'" tabindex="0">
    <table>
      <tbody>
        <tr v-for="(l, i) in lines" :key="i">
          <td class="ln" aria-hidden="true">{{ i + 1 }}</td>
          <!-- eslint-disable-next-line vue/no-v-html -- highlight.js escapes the source -->
          <td class="src"><code v-html="l || ' '" /></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.code {
  max-height: 620px;
  overflow: auto;
  background: #fbfcfd;
  border: 1px solid var(--rule-soft);
  border-radius: 4px;
  font-family: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12.5px;
  line-height: 1.6;
}

table {
  border-collapse: collapse;
  width: 100%;
}

.ln {
  position: sticky;
  left: 0;
  width: 1%;
  padding: 0 12px 0 14px;
  background: #f3f5f8;
  color: #9aa4b2;
  text-align: right;
  user-select: none;
  vertical-align: top;
}

.src {
  padding: 0 14px;
  white-space: pre;
}
</style>
