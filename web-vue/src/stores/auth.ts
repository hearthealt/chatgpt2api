import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { authApi } from '@/api/auth'
import { getAuthToken } from '@/api/client'
import {
  clearLastSubject,
  purgeUserScopedPreferences,
  reconcileSubjectScopedPreferences,
} from '@/lib/preferences'
import type { AuthStatusResponse } from '@/types/api'

type AuthRole = 'admin' | 'user' | ''

function normalizeRole(value: unknown): AuthRole {
  const role = String(value || '').trim().toLowerCase()
  return role === 'admin' || role === 'user' ? role : ''
}

export const useAuthStore = defineStore('auth', () => {
  const isLoggedIn = ref(false)
  const isLoading = ref(false)
  const role = ref<AuthRole>('')
  const subjectId = ref('')
  const name = ref('')
  const lastCheckedAt = ref(0)
  const AUTH_CACHE_MS = 60000
  let checkPromise: Promise<boolean> | null = null

  const isAdmin = computed(() => role.value === 'admin')
  const isUser = computed(() => role.value === 'user')

  function applyStatus(status: AuthStatusResponse | undefined | null) {
    isLoggedIn.value = Boolean(status?.authenticated)
    role.value = isLoggedIn.value ? normalizeRole(status?.role) : ''
    subjectId.value = isLoggedIn.value ? String(status?.subject_id || '') : ''
    name.value = isLoggedIn.value ? String(status?.name || '') : ''
    // 切换账号时清除上一个用户的本地会话数据，避免串号
    if (isLoggedIn.value && subjectId.value) {
      reconcileSubjectScopedPreferences(subjectId.value)
    }
  }

  function clearIdentity() {
    isLoggedIn.value = false
    role.value = ''
    subjectId.value = ''
    name.value = ''
  }

  // 登录：支持用户名+密码，或直接粘贴密钥（admin）
  async function login(credential: string | { username?: string; password: string }) {
    isLoading.value = true
    try {
      const payload = typeof credential === 'string' ? { password: credential } : credential
      await authApi.login(payload)
      const status = await authApi.checkAuth()
      applyStatus(status)
      lastCheckedAt.value = Date.now()
      return isLoggedIn.value
    } catch (error) {
      clearIdentity()
      throw error
    } finally {
      isLoading.value = false
    }
  }

  // 自助注册
  async function register(payload: { username: string; password: string; invite_code?: string }) {
    isLoading.value = true
    try {
      await authApi.register(payload)
      const status = await authApi.checkAuth()
      applyStatus(status)
      lastCheckedAt.value = Date.now()
      return isLoggedIn.value
    } catch (error) {
      clearIdentity()
      throw error
    } finally {
      isLoading.value = false
    }
  }

  // 登出
  async function logout() {
    try {
      await authApi.logout()
    } finally {
      clearIdentity()
      lastCheckedAt.value = 0
      // 登出时清除本地会话数据，避免下一个账号在同一浏览器看到历史
      purgeUserScopedPreferences()
      clearLastSubject()
    }
  }

  // 检查登录状态
  async function checkAuth() {
    if (!getAuthToken()) {
      clearIdentity()
      lastCheckedAt.value = 0
      checkPromise = null
      return false
    }
    const now = Date.now()
    if (now - lastCheckedAt.value < AUTH_CACHE_MS) {
      return isLoggedIn.value
    }
    if (checkPromise) {
      return checkPromise
    }
    try {
      checkPromise = (async () => {
        const status = await authApi.checkAuth()
        applyStatus(status)
        lastCheckedAt.value = Date.now()
        return isLoggedIn.value
      })()
      return await checkPromise
    } catch (error) {
      clearIdentity()
      lastCheckedAt.value = 0
      return false
    } finally {
      checkPromise = null
    }
  }

  return {
    isLoggedIn,
    isLoading,
    role,
    subjectId,
    name,
    isAdmin,
    isUser,
    login,
    register,
    logout,
    checkAuth,
    clearIdentity,
  }
})
