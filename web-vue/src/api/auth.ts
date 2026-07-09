import apiClient, { clearAuthToken, setAuthToken } from './client'
import type {
  AuthStatusResponse,
  LoginRequest,
  LoginResponse,
  RegisterRequest,
  RegisterResponse,
} from '@/types/api'

export const authApi = {
  async login(data: LoginRequest) {
    // 用户名+密码：先请求拿会话令牌，再写入本地
    if (data.username) {
      const response = await apiClient.post<LoginRequest, LoginResponse>('/auth/login', {
        username: data.username,
        password: data.password || '',
      })
      if (response.token) {
        setAuthToken(response.token)
      }
      return response
    }
    // 兼容：直接粘贴管理员密钥 / API key 登录
    setAuthToken(data.password || '')
    try {
      return await apiClient.post<never, LoginResponse>('/auth/login')
    } catch (error) {
      clearAuthToken()
      throw error
    }
  },

  async register(data: RegisterRequest) {
    const response = await apiClient.post<RegisterRequest, RegisterResponse>('/auth/register', data)
    if (response.token) {
      setAuthToken(response.token)
    }
    return response
  },

  logout: () => {
    clearAuthToken()
    return Promise.resolve({ ok: true })
  },

  checkAuth: () => apiClient.get<never, AuthStatusResponse>('/auth/status', { timeout: 8000 }),

  registerInfo: () =>
    apiClient.get<never, { open_registration: boolean; require_invite: boolean }>('/auth/register-info'),
}
