import apiClient from './client'
import type { MeResponse, MyGalleryResponse, MyUsageResponse } from '@/types/api'

export const accountApi = {
  getMe: () => apiClient.get<never, MeResponse>('/api/me'),

  getUsage: (limit = 50) =>
    apiClient.get<never, MyUsageResponse>('/api/me/usage', { params: { limit } }),

  getGallery: (limit = 60, offset = 0) =>
    apiClient.get<never, MyGalleryResponse>('/api/me/gallery', { params: { limit, offset } }),

  changePassword: (oldPassword: string, newPassword: string) =>
    apiClient.post<{ old_password: string; new_password: string }, { ok: boolean; token?: string }>(
      '/api/me/password',
      { old_password: oldPassword, new_password: newPassword },
    ),

  regenerateApiKey: () =>
    apiClient.post<never, { ok: boolean; key: string; api_keys: MeResponse['api_keys'] }>('/api/me/api-key'),

  getConversations: () =>
    apiClient.get<never, { conversations: any[] }>('/api/me/conversations'),

  saveConversations: (conversations: any[]) =>
    apiClient.put<{ conversations: any[] }, { ok: boolean; count: number }>('/api/me/conversations', { conversations }),
}
