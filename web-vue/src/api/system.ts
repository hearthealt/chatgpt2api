import apiClient from './client'

export interface VersionInfo {
  version: string
  changelog_url: string
}

export interface UpdateStatus {
  status: string
  message: string
  new_version?: string
}

export interface LatestVersionInfo {
  status: string
  version?: string
  changelog?: string
  message?: string
}

/**
 * 获取当前系统版本
 */
export function getVersion(): Promise<VersionInfo> {
  return apiClient.get<never, VersionInfo>('/version')
}

/**
 * 获取云端最新版本（由后端代理请求 GitHub）
 */
export function getLatestVersion(): Promise<LatestVersionInfo> {
  return apiClient.get<never, LatestVersionInfo>('/api/system/latest-version')
}

/**
 * 触发系统更新
 */
export function triggerUpdate(): Promise<UpdateStatus> {
  return apiClient.post<never, UpdateStatus>('/api/system/update')
}
