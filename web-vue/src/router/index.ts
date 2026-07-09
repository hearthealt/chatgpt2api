import { createRouter, createWebHashHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const adminHome = { name: 'dashboard' }
const userHome = { name: 'studio' }
const userAllowedRoutes = new Set(['studio', 'my-gallery', 'my-usage', 'account-settings'])

const router = createRouter({
  history: createWebHashHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/public/uptime',
      name: 'public-uptime',
      component: () => import('@/views/PublicUptime.vue'),
      meta: { requiresAuth: false },
    },
    {
      path: '/public/logs',
      name: 'public-logs',
      component: () => import('@/views/PublicLogs.vue'),
      meta: { requiresAuth: false },
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/Login.vue'),
      meta: { requiresAuth: false },
    },
    {
      path: '/register-user',
      name: 'user-register',
      component: () => import('@/views/UserRegister.vue'),
      meta: { requiresAuth: false },
    },
    {
      path: '/',
      component: () => import('@/layouts/AppShell.vue'),
      meta: { requiresAuth: true },
      children: [
        {
          path: '',
          name: 'dashboard',
          component: () => import('@/views/Dashboard.vue'),
          meta: { adminOnly: true },
        },
        {
          path: 'accounts',
          name: 'accounts',
          component: () => import('@/views/Accounts.vue'),
          meta: { adminOnly: true },
        },
        {
          path: 'users',
          name: 'users',
          component: () => import('@/views/UsersAdmin.vue'),
          meta: { adminOnly: true },
        },
        {
          path: 'settings',
          name: 'settings',
          component: () => import('@/views/Settings.vue'),
          meta: { adminOnly: true },
        },
        {
          path: 'proxy',
          name: 'proxy',
          component: () => import('@/views/Proxy.vue'),
          meta: { adminOnly: true },
        },
        {
          path: 'providers',
          name: 'providers',
          component: () => import('@/views/Providers.vue'),
          meta: { adminOnly: true },
        },
        {
          path: 'register',
          name: 'register',
          component: () => import('@/views/Register.vue'),
          meta: { adminOnly: true },
        },
        {
          path: 'logs',
          name: 'logs',
          component: () => import('@/views/Logs.vue'),
          meta: { adminOnly: true },
        },
        {
          path: 'monitor',
          name: 'monitor',
          component: () => import('@/views/Monitor.vue'),
          meta: { adminOnly: true },
        },
        {
          path: 'docs',
          name: 'docs',
          component: () => import('@/views/Docs.vue'),
          meta: { adminOnly: true },
        },
        {
          path: 'gallery',
          name: 'gallery',
          component: () => import('@/views/Gallery.vue'),
          meta: { adminOnly: true },
        },
        {
          path: 'debug',
          name: 'debug',
          component: () => import('@/views/DebugCenter.vue'),
          meta: { adminOnly: true },
        },
        {
          path: 'studio',
          name: 'studio',
          component: () => import('@/views/Studio.vue'),
          meta: {},
        },
        {
          path: 'my-gallery',
          name: 'my-gallery',
          component: () => import('@/views/MyGallery.vue'),
          meta: {},
        },
        {
          path: 'my-usage',
          name: 'my-usage',
          component: () => import('@/views/MyUsage.vue'),
          meta: {},
        },
        {
          path: 'account-settings',
          name: 'account-settings',
          component: () => import('@/views/AccountSettings.vue'),
          meta: {},
        },
      ],
    },
  ],
})

router.beforeEach(async (to) => {
  const authStore = useAuthStore()

  if (to.name === 'login') {
    if (authStore.isLoggedIn) {
      return authStore.isUser ? userHome : adminHome
    }
    const loggedIn = await authStore.checkAuth()
    if (loggedIn) {
      return authStore.isUser ? userHome : adminHome
    }
    return true
  }

  const requiresAuth = to.matched.some((record) => record.meta.requiresAuth !== false)
  if (!requiresAuth) {
    return true
  }

  const loggedIn = authStore.isLoggedIn || await authStore.checkAuth()
  if (!loggedIn) {
    const redirect = to.fullPath || '/'
    return { name: 'login', query: { redirect } }
  }

  if (authStore.isUser && !userAllowedRoutes.has(String(to.name || ''))) {
    return userHome
  }

  const requiresAdmin = to.matched.some((record) => record.meta.adminOnly)
  if (requiresAdmin && !authStore.isAdmin) {
    return userHome
  }

  // Fast path: don't block route switch on auth probe.
  void authStore.checkAuth()

  return true
})

export default router
