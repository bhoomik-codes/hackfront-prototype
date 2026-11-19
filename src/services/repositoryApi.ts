import type { AnalysisJob, ApiHealth, GraphMode, RepositoryGraphData, RepositorySource } from '../types'

type ApiErrorPayload = { detail?: { message?: string; code?: string } | string }

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
    response = await fetch(path, { ...init, credentials: 'include', headers: { 'Content-Type': 'application/json', ...init?.headers }, cache: 'no-store' })
  } catch {
    throw new RepositoryApiError('The analysis service could not be reached. Start the app’s API server and try again.', 'api_unavailable', 0)
  }
  const data = await response.json().catch(() => null) as T | ApiErrorPayload | null
  if (!response.ok) {
    const detail = (data as ApiErrorPayload | null)?.detail
    const message = typeof detail === 'object' && detail?.message ? detail.message : typeof detail === 'string' ? detail : `The request failed (${response.status}).`
    const code = typeof detail === 'object' && detail?.code ? detail.code : 'request_failed'
    throw new RepositoryApiError(message, code, response.status)
  }
  if (response.status === 204) return undefined as T
  if (data === null) throw new RepositoryApiError('The analysis service returned an unreadable response.', 'invalid_response', response.status)
  return data as T
}

export const startBrowserSession = () => request<{ ready: boolean }>('/api/session')
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

export const deleteRepository = (owner: string, repository: string) => request<void>(
  `/api/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}`,
  { method: 'DELETE' },
)
