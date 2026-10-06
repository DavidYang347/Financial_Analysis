import type { AppModule } from './types'

// Every src/modules/*/module.ts is registered automatically.
const found = import.meta.glob<{ default: AppModule }>('./*/module.ts', { eager: true })

export const modules: AppModule[] = Object.values(found)
  .map((m) => m.default)
  .sort((a, b) => a.order - b.order)
