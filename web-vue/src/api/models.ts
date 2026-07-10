import apiClient from './client'

export interface OpenAIModel {
  id: string
  object?: string
  created?: number
  owned_by?: string
  root?: string
  parent?: string | null
}

export interface ModelListResponse {
  object?: string
  data: OpenAIModel[]
}

export interface ModelCatalogResponse {
  object?: 'model_catalog' | string
  chat_models: string[]
  image_models: string[]
  all_models?: string[]
  source?: {
    chat?: string
    image?: string
  }
  openai_models_endpoint?: string
}

export const modelsApi = {
  catalog: () => apiClient.get<never, ModelCatalogResponse>('/api/model-catalog'),
  catalogAll: () => apiClient.get<never, ModelCatalogResponse>('/api/model-catalog/all'),
  list: () => apiClient.get<never, ModelListResponse>('s'),
}

export interface ModelCatalogSettings {
  enabled_models?: string[]
  disabled_models?: string[]
  deleted_models?: string[]
  default_user_models?: string[]
  custom_chat_models?: string[]
  custom_image_models?: string[]
}

export const modelCatalogApi = {
  get: () => apiClient.get<never, ModelCatalogSettings>('/api/admin/model-catalog'),
  save: (data: ModelCatalogSettings) => apiClient.post<ModelCatalogSettings, ModelCatalogSettings>('/api/admin/model-catalog', data),
}
