import apiClient from './client'

export interface Provider {
  name: string
  base_url: string
  api_key: string
  models: string[]
  image_models: string[]
  enabled: boolean
  timeout_secs: number
  proxy: string
}

export type ProviderPayload = Partial<Provider> & {
  original_name?: string
  create_only?: boolean
}

export interface ProviderTestResult {
  ok: boolean
  status: number
  latency_ms: number
  model_count?: number
  error?: string | null
}

export interface ProviderModelsResult {
  ok: boolean
  status: number
  models: string[]
  error?: string | null
}

export const providersApi = {
  list: () =>
    apiClient.get<never, { providers: Provider[] }>('/api/providers'),

  save: (payload: ProviderPayload) =>
    apiClient.post<ProviderPayload, { provider: Provider; providers: Provider[] }>(
      '/api/providers',
      payload,
    ),

  delete: (name: string) =>
    apiClient.delete<never, { deleted: string; providers: Provider[] }>(
      `/api/providers/${encodeURIComponent(name)}`,
    ),

  test: (payload: { base_url: string; api_key?: string; proxy?: string; model?: string }) =>
    apiClient.post<typeof payload, { result: ProviderTestResult }>(
      '/api/providers/test',
      payload,
    ),

  fetchModels: (payload: { base_url: string; api_key?: string; proxy?: string }) =>
    apiClient.post<typeof payload, ProviderModelsResult>(
      '/api/providers/models',
      payload,
    ),
}
