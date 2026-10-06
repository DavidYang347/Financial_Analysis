import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { modules } from './modules'

const routes: RouteRecordRaw[] = [
  { path: '/', redirect: modules[0]?.path ?? '/' },
  ...modules.flatMap((m) => m.routes),
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  const mod = modules.find((m) => to.path.startsWith(m.path))
  const sub = typeof to.meta.title === 'string' ? to.meta.title : ''
  document.title = [sub, mod?.title, 'Financial Analysis'].filter(Boolean).join(' - ')
})
