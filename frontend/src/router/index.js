import { createRouter, createWebHistory } from 'vue-router'
import { systemMode, isPathAllowed, fallbackPath } from '../composables/useSystemMode'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/Login.vue'),
  },
  {
    path: '/',
    component: () => import('../views/Layout.vue'),
    redirect: '/dashboard',
    children: [
      { path: 'dashboard', name: 'Dashboard', component: () => import('../views/Dashboard.vue'), meta: { title: 'Central Maintenance' } },
      { path: 'fault', name: 'FaultDiagnosis', component: () => import('../views/FaultDiagnosis.vue'), meta: { title: 'Failure Reports' } },
      { path: 'params', name: 'ParamMonitor', component: () => import('../views/ParamMonitor.vue'), meta: { title: 'Condition Monitoring' } },
      { path: 'events', name: 'EventReports', component: () => import('../views/EventReports.vue'), meta: { title: 'Event Reports' } },
      { path: 'config', name: 'ConfigManagement', component: () => import('../views/ConfigManagement.vue'), meta: { title: 'Configuration Reports' } },
      { path: 'groundtest', name: 'StartupTest', component: () => import('../views/StartupTest.vue'), meta: { title: 'Ground Test' } },
      { path: 'dataload', name: 'DataLoad', component: () => import('../views/DataLoad.vue'), meta: { title: 'Data Load' } },
      { path: 'lifecycle', name: 'Lifecycle', component: () => import('../views/Lifecycle.vue'), meta: { title: 'Time Cycle' } },
      { path: 'acars', name: 'ACARS', component: () => import('../views/ACARS.vue'), meta: { title: 'ACARS' } },
      { path: 'print', name: 'Print', component: () => import('../views/Print.vue'), meta: { title: 'Print' } },
      { path: 'icd', name: 'ICD', component: () => import('../views/ICDManagement.vue'), meta: { title: 'ICD' } },
      { path: 'utility', name: 'Utility', component: () => import('../views/Dashboard.vue'), meta: { title: 'Utility' } },
      { path: 'lru', name: 'LRU', component: () => import('../views/FaultDiagnosis.vue'), meta: { title: 'LRU Fault History' } },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to, from, next) => {
  // 登录校验
  const token = localStorage.getItem('ahmu_token')
  if (to.path !== '/login' && !token) {
    next('/login')
    return
  }

  // 模式访问控制:
  // - 维护模式: 仅可访问地面测试与数据加载页面
  // - 正常模式: 可访问除地面测试和数据加载外的其它页面
  if (to.path !== '/login' && !isPathAllowed(to.path)) {
    next(fallbackPath())
    return
  }
  next()
})

export default router
