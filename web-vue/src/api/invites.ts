import apiClient from './client'

export interface InviteCode {
  code: string
  max_uses: number
  used_count: number
  unlimited: boolean
  remaining: number | null
  enabled: boolean
  created_at?: string
  note?: string
  exhausted: boolean
}

export interface InvitesResponse {
  items: InviteCode[]
}

export interface InviteGenerateResponse extends InvitesResponse {
  created: InviteCode[]
}

export const invitesApi = {
  list: () => apiClient.get<never, InvitesResponse>('/api/admin/invites'),

  generate: (payload: { count: number; max_uses: number; note?: string }) =>
    apiClient.post<{ count: number; max_uses: number; note?: string }, InviteGenerateResponse>(
      '/api/admin/invites',
      payload,
    ),

  toggle: (code: string, enabled: boolean) =>
    apiClient.post<{ enabled: boolean }, InvitesResponse>(`/api/admin/invites/${encodeURIComponent(code)}/toggle`, {
      enabled,
    }),

  delete: (code: string) =>
    apiClient.delete<never, InvitesResponse>(`/api/admin/invites/${encodeURIComponent(code)}`),
}

export interface UserAccessSettings {
  open_registration: boolean
  require_invite: boolean
  period: string
  default_call_limit: number
  default_image_limit: number
  invite_code?: string
  session_ttl_days?: number
}

export const userAccessApi = {
  get: () => apiClient.get<never, { settings: UserAccessSettings }>('/api/admin/user-access'),

  save: (payload: Partial<UserAccessSettings>) =>
    apiClient.post<Partial<UserAccessSettings>, { settings: UserAccessSettings }>('/api/admin/user-access', payload),
}
