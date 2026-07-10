import apiClient from './client'
import type { AdminUser } from '@/types/api'

export interface AdminUsersResponse {
  items: AdminUser[]
}

export interface AdminUserQuotaPayload {
  call_limit?: number
  image_limit?: number
  period?: string
}

export interface AdminUserCreatePayload {
  username: string
  password: string
  role?: string
  quota?: AdminUserQuotaPayload
  allowed_models?: string[]
}

export interface AdminUserUpdatePayload {
  username?: string
  enabled?: boolean
  role?: string
  quota?: AdminUserQuotaPayload
  allowed_models?: string[]
}

export interface AdminUserCreateResponse extends AdminUsersResponse {
  item: AdminUser
  api_key?: string
}

export interface UserStatsResponse {
  counts: { total: number; enabled: number; disabled: number; admins: number; new_today: number }
  top_users: Array<{ username: string; calls: number; images: number; total: number }>
  active_trend: { labels: string[]; values: number[] }
  rank_days: number
  trend_days: number
}

export const adminUsersApi = {
  list: () => apiClient.get<never, AdminUsersResponse>('/api/admin/users'),

  stats: () => apiClient.get<never, UserStatsResponse>('/api/admin/user-stats'),

  create: (payload: AdminUserCreatePayload) =>
    apiClient.post<AdminUserCreatePayload, AdminUserCreateResponse>('/api/admin/users', payload),

  update: (userId: string, payload: AdminUserUpdatePayload) =>
    apiClient.post<AdminUserUpdatePayload, { item: AdminUser } & AdminUsersResponse>(
      `/api/admin/users/${userId}`,
      payload,
    ),

  resetPassword: (userId: string, newPassword: string) =>
    apiClient.post<{ new_password: string }, { ok: boolean; item: AdminUser }>(
      `/api/admin/users/${userId}/password`,
      { new_password: newPassword },
    ),

  delete: (userId: string) =>
    apiClient.delete<never, AdminUsersResponse>(`/api/admin/users/${userId}`),
}
