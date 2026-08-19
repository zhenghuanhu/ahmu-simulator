import { createRouter, createWebHistory } from 'vue-router'

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
  const token = localStorage.getItem('ahmu_token')
  if (to.path !== '/login' && !token) {
    next('/login')
  } else {
    next()
  }
})

export default router
