import type { Component } from 'vue'
import type { RouteRecordRaw } from 'vue-router'

/**
 * A top-level module shown in the left rail.
 *
 * To add a module, create `src/modules/<id>/module.ts` that default-exports
 * an AppModule. It is picked up automatically (see ./index.ts).
 */
export interface AppModule {
  id: string
  title: string
  icon: Component
  /** Position in the rail, ascending. */
  order: number
  /** Route prefix that marks the module as active, e.g. "/data". */
  path: string
  routes: RouteRecordRaw[]
}
