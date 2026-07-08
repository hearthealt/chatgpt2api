import apiClient from './client'

export type RegisterProvider = {
  id?: string
  provider_id?: string
  enable?: boolean
  type?: string
  label?: string
  api_base?: string
  api_key?: string
  admin_password?: string
  product_id?: number | string
  domain?: string[]
  proxy?: string
  mail_mode?: string
  email_type?: string
  mail_domain?: string
  max_retry?: number
  [key: string]: unknown
}

export type LegacyRegisterConfig = {
  mail: {
    request_timeout?: number
    wait_timeout?: number
    wait_interval?: number
    user_agent?: string
    providers?: RegisterProvider[]
    [key: string]: unknown
  }
  proxy: string
  total: number
  threads: number
  mode: 'total' | 'quota' | 'available' | string
  target_quota: number
  target_available: number
  check_interval: number
  enabled: boolean
  stats?: {
    success?: number
    fail?: number
    done?: number
    running?: number
    threads?: number
    elapsed_seconds?: number
    avg_seconds?: number
    success_rate?: number
    current_quota?: number
    current_available?: number
    [key: string]: unknown
  }
  logs?: Array<{
    time: string
    text: string
    level?: string
  }>
}

export type AutoRegisterStatus = {
  enabled: boolean
  last_triggered_at: string | null
  last_completed_at: string | null
  last_trigger_reason: string
  running: boolean
  consecutive_failures: number
  last_failure_reset_at: string | null
  total_auto_registered: number
  trigger_count_by_reason: {
    no_account: number
    all_quota_exhausted: number
    all_accounts_invalid: number
    all_accounts_rate_limited: number
    all_accounts_busy: number
    min_available_threshold: number
  }
  config: {
    enabled: boolean
    trigger_conditions: {
      no_account: boolean
      all_quota_exhausted: boolean
      all_accounts_invalid: boolean
      all_accounts_rate_limited: boolean
      all_accounts_busy: boolean
    }
    register_count: number
    cooldown_seconds: number
    max_total_accounts: number
    min_available_accounts: number
    max_failures: number
    reset_failures_after: number
  }
}

export const registerApi = {
  getConfig() {
    return apiClient.get<any, { register: LegacyRegisterConfig }>('/api/register')
  },
  updateConfig(payload: Partial<LegacyRegisterConfig>) {
    return apiClient.post<any, { register: LegacyRegisterConfig }>('/api/register', payload)
  },
  startLegacy() {
    return apiClient.post<any, { register: LegacyRegisterConfig }>('/api/register/start')
  },
  stopLegacy() {
    return apiClient.post<any, { register: LegacyRegisterConfig }>('/api/register/stop')
  },
  resetLegacy() {
    return apiClient.post<any, { register: LegacyRegisterConfig }>('/api/register/reset')
  },
  getAutoRegisterStatus() {
    return apiClient.get<any, { status: AutoRegisterStatus }>('/api/register/auto-register/status')
  },
  updateAutoRegisterConfig(payload: Partial<AutoRegisterStatus['config']>) {
    return apiClient.post<any, { status: AutoRegisterStatus }>('/api/register/auto-register/config', payload)
  },
  triggerAutoRegister(count?: number) {
    return apiClient.post<any, { triggered: boolean; message: string }>('/api/register/auto-register/trigger', { count })
  },
  resetAutoRegisterFailures() {
    return apiClient.post<any, { status: AutoRegisterStatus }>('/api/register/auto-register/reset-failures')
  },
}
