export const preferenceKeys = {
  sidebarCollapsed: 'sidebar-collapsed',
  accountsViewMode: 'accounts-view-mode',
  accountsPageSize: 'accounts-page-size',
  systemLogLimit: 'system-log-limit',
  runtimeLogLimit: 'runtime-log-limit',
  galleryPageSize: 'gallery-page-size',
  publicLogFoldState: 'public-log-fold-state',
  imageTaskLocalIds: 'image-task-local-ids',
  imageTaskConversations: 'image-task-conversations',
  imageTaskActiveConversationId: 'image-task-active-conversation-id',
  studioActiveMode: 'studio-active-mode',
  studioActiveConversationId: 'studio-active-conversation-id',
  studioChatModel: 'studio-chat-model',
  studioChatReasoningEffort: 'studio-chat-reasoning-effort',
  studioConversationBadges: 'studio-conversation-badges',
  studioConversations: 'studio-conversations',
  studioFullscreen: 'studio-fullscreen',
  studioImageModel: 'studio-image-model',
  studioSidebarWidth: 'studio-sidebar-width',
  themeMode: 'theme-mode',
} as const

type PreferenceKey = typeof preferenceKeys[keyof typeof preferenceKeys]

function storage(): Storage | null {
  if (typeof window === 'undefined') return null
  try {
    return window.localStorage
  } catch {
    return null
  }
}

export function getStringPreference(key: PreferenceKey, fallback = ''): string {
  const value = storage()?.getItem(key)
  return value == null ? fallback : value
}

export function setStringPreference(key: PreferenceKey, value: string): void {
  storage()?.setItem(key, value)
}

export function getBooleanPreference(key: PreferenceKey, fallback = false): boolean {
  const value = getStringPreference(key)
  if (!value) return fallback
  return value === 'true'
}

export function setBooleanPreference(key: PreferenceKey, value: boolean): void {
  setStringPreference(key, value ? 'true' : 'false')
}

export function getNumberPreference(
  key: PreferenceKey,
  fallback: number,
  options: { allowed?: readonly number[]; min?: number; max?: number } = {},
): number {
  const parsed = Number(getStringPreference(key))
  if (!Number.isFinite(parsed)) return fallback
  const next = Math.trunc(parsed)
  if (options.allowed && !options.allowed.includes(next)) return fallback
  if (typeof options.min === 'number' && next < options.min) return fallback
  if (typeof options.max === 'number' && next > options.max) return fallback
  return next
}

export function setNumberPreference(key: PreferenceKey, value: number): void {
  setStringPreference(key, String(value))
}

export function getJsonPreference<T>(key: PreferenceKey, fallback: T): T {
  const value = getStringPreference(key)
  if (!value) return fallback
  try {
    return JSON.parse(value) as T
  } catch {
    return fallback
  }
}

export function setJsonPreference(key: PreferenceKey, value: unknown): void {
  setStringPreference(key, JSON.stringify(value))
}

export function removePreference(key: PreferenceKey): void {
  storage()?.removeItem(key)
}

// 与登录用户绑定的本地数据（对话、画图会话等）。切换账号或登出时需要清除，避免串号。
const userScopedPreferenceKeys: PreferenceKey[] = [
  preferenceKeys.studioConversations,
  preferenceKeys.studioConversationBadges,
  preferenceKeys.studioActiveConversationId,
  preferenceKeys.studioActiveMode,
  preferenceKeys.imageTaskLocalIds,
  preferenceKeys.imageTaskConversations,
  preferenceKeys.imageTaskActiveConversationId,
]

export function purgeUserScopedPreferences(): void {
  const store = storage()
  if (!store) return
  for (const key of userScopedPreferenceKeys) {
    store.removeItem(key)
  }
}

const LAST_SUBJECT_KEY = 'auth-last-subject-id'

// 若当前登录主体与上次不同，则清除上一个用户的本地会话数据。
export function reconcileSubjectScopedPreferences(subjectId: string): void {
  const store = storage()
  if (!store) return
  const normalized = String(subjectId || '').trim()
  const previous = store.getItem(LAST_SUBJECT_KEY) || ''
  if (normalized && previous && previous !== normalized) {
    purgeUserScopedPreferences()
  }
  if (normalized) {
    store.setItem(LAST_SUBJECT_KEY, normalized)
  }
}

export function clearLastSubject(): void {
  storage()?.removeItem(LAST_SUBJECT_KEY)
}
