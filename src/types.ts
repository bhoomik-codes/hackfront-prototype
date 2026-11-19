export type GraphMode = 'architecture' | 'decisions'

export type RepositoryGraphNode = {
  id: string
  label: string
  kind: string
  path?: string | null
  description?: string | null
  metadata: Record<string, string | number | boolean | null>
}

export type RepositoryGraphEdge = {
  id: string
  source: string
  target: string
  kind: string
  label: string
}

export type RepositoryGraphData = {
  repository: Record<string, string | number | boolean | null>
  nodes: RepositoryGraphNode[]
  edges: RepositoryGraphEdge[]
  coverage: Record<string, string | number | boolean>
}

export type AnalysisJob = {
  job_id: string
  status: 'queued' | 'running' | 'complete' | 'failed'
  stage: string
  progress: number
  message: string
  repository: string | null
  error_code: string | null
  counts?: Record<string, number>
  coverage?: Record<string, number | boolean | string>
}

export type RepositorySource = {
  path: string
  language: string
  content: string
  truncated: boolean
}

export type ApiHealth = {
  status: 'ready' | 'degraded'
  neo4j_configured: boolean
  message?: string
}
