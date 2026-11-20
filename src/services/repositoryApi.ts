import type { AnalysisJob, ApiHealth, GraphMode, RepositoryGraphData, RepositorySource, RepositorySummary } from '../types'

type ApiErrorPayload = { detail?: { message?: string; code?: string } | string | unknown[] }
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? ''

export class RepositoryApiError extends Error {
  readonly code: string
  readonly status: number

  constructor(message: string, code: string, status: number) {
    super(message)
    this.name = 'RepositoryApiError'
    this.code = code
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...init, credentials: 'include', headers: { 'Content-Type': 'application/json', ...init?.headers }, cache: 'no-store' })
  } catch {
    throw new RepositoryApiError('The analysis service could not be reached. Start the app’s API server and try again.', 'api_unavailable', 0)
  }
  const data = await response.json().catch(() => null) as T | ApiErrorPayload | null
  if (!response.ok) {
    const detail = (data as ApiErrorPayload | null)?.detail
    const message = detail && !Array.isArray(detail) && typeof detail === 'object' && detail.message ? detail.message : typeof detail === 'string' ? detail : response.status === 422 ? 'Some details are invalid. Check the fields and try again.' : response.status === 401 ? 'Your session has expired. Sign in again to continue.' : response.status === 403 ? 'You do not have permission for this action.' : response.status === 404 ? 'This item is no longer available. Refresh the list and try again.' : response.status === 429 ? 'Too many requests. Wait a moment before trying again.' : response.status >= 500 ? 'The service is temporarily unavailable. Please try again shortly.' : 'This action could not be completed. Check your details and try again.'
    const code = detail && !Array.isArray(detail) && typeof detail === 'object' && detail.code ? detail.code : 'request_failed'
    throw new RepositoryApiError(message, code, response.status)
  }
  if (response.status === 204) return undefined as T
  if (data === null) throw new RepositoryApiError('The analysis service returned an unreadable response.', 'invalid_response', response.status)
  return data as T
}

export type AuthAvailability = {
  ready: boolean
  authenticated?: boolean
  login?: string | null
  oauth_configured?: boolean
  github_oauth_configured?: boolean
  google_oauth_configured?: boolean
  email_auth_configured?: boolean
  auth_provider?: 'github' | 'google' | 'email' | null
}

export const startBrowserSession = () => request<AuthAvailability>('/api/session')
const redirectToProvider = (provider: 'github' | 'google') => {
  try { sessionStorage.setItem('why-oauth-redirect', 'true') } catch { /* redirect still works when storage is disabled */ }
  window.location.assign(`${API_BASE_URL}/api/auth/${provider}`)
}
export const loginWithGitHub = () => redirectToProvider('github')
export const loginWithGoogle = () => redirectToProvider('google')
export const registerEmailAccount = (email: string, password: string) => request<{ message: string }>('/api/auth/email/register', {
  method: 'POST', body: JSON.stringify({ email, password }),
})
export const verifyEmailSignup = (email: string, code: string) => request<{ authenticated: boolean; login: string }>('/api/auth/email/verify', {
  method: 'POST', body: JSON.stringify({ email, code }),
})
export const loginWithEmail = (email: string, password: string) => request<{ authenticated: boolean; requires_2fa: boolean; login: string }>('/api/auth/email/login', {
  method: 'POST', body: JSON.stringify({ email, password }),
})
export const verifyEmailTwoFactor = (email: string, code: string) => request<{ authenticated: boolean; login: string }>('/api/auth/email/2fa/verify', {
  method: 'POST', body: JSON.stringify({ email, code }),
})
export type AccountSettings = { email: string | null; provider: string | null; username: string; two_factor_enabled: boolean; two_factor_available: boolean }
export const getAccountSettings = () => request<AccountSettings>('/api/account')
export const updateAccountProfile = (username: string) => request<{ username: string }>('/api/account/profile', {
  method: 'PUT', body: JSON.stringify({ username }),
})
export const changeAccountPassword = (currentPassword: string, newPassword: string) => request<void>('/api/account/password', {
  method: 'PUT', body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
})
export const deleteAccountRepositories = () => request<{ deleted: number }>('/api/account/repositories', { method: 'DELETE' })
export const deleteAccount = (confirmation: 'DELETE', password?: string) => request<void>('/api/account', {
  method: 'DELETE', body: JSON.stringify({ confirmation, ...(password ? { password } : {}) }),
})
export const updateTwoFactor = (enabled: boolean, password: string) => request<{ two_factor_enabled: boolean; enrollment_required?: boolean }>('/api/account/two-factor', {
  method: 'POST', body: JSON.stringify({ enabled, password }),
})
export const confirmTwoFactorEnrollment = (code: string) => request<{ two_factor_enabled: boolean }>('/api/account/two-factor/confirm', {
  method: 'POST', body: JSON.stringify({ code }),
})
export const logout = () => request<void>('/api/auth/logout', { method: 'POST' })
export const checkApiHealth = () => request<ApiHealth>('/api/health')

export const startRepositoryAnalysis = (repositoryUrl: string) => request<{ job_id: string; status: 'queued' }>('/api/repositories/analyze', {
  method: 'POST',
  body: JSON.stringify({ repository_url: repositoryUrl }),
})

export const getAnalysisJob = (jobId: string) => request<AnalysisJob>(`/api/jobs/${encodeURIComponent(jobId)}`)

export const getRepositoryGraph = (owner: string, repository: string, mode: GraphMode) => request<RepositoryGraphData>(
  `/api/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/graph?mode=${mode}`,
)

export const getRepositoryFile = (owner: string, repository: string, path: string) => {
  const encodedPath = path.split('/').map(encodeURIComponent).join('/')
  return request<RepositorySource>(`/api/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/files/${encodedPath}`)
}

export const getRepositories = () => request<RepositorySummary[]>('/api/repositories')

export const deleteRepository = (owner: string, repository: string) => request<void>(
  `/api/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}`,
  { method: 'DELETE' },
)

export const saveRepository = (owner: string, repository: string) => request<{ saved: boolean }>(
  `/api/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/save`,
  { method: 'POST', body: '{}' },
)
