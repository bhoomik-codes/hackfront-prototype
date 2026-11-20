import { useEffect, useMemo, useRef, useState } from 'react'
import type { Core, ElementDefinition } from 'cytoscape'
import type { GraphMode, RepositoryGraphData, RepositoryGraphNode, RepositorySource, RepositorySummary } from './types'
import { checkApiHealth, confirmTwoFactorEnrollment, deleteRepository, getAccountSettings, getAnalysisJob, getRepositories, getRepositoryFile, getRepositoryGraph, loginWithEmail, loginWithGitHub, loginWithGoogle, logout, registerEmailAccount, RepositoryApiError, saveRepository, startBrowserSession, startRepositoryAnalysis, updateTwoFactor, verifyEmailSignup, verifyEmailTwoFactor, type AccountSettings } from './services/repositoryApi'
import { askWithEvidence, testModelConnection, type AssistantSettings, type EvidenceCitation } from './services/evidenceAssistant'
import {
  ArrowDown, ArrowRight, ArrowsOut, BookOpenText,
  CheckCircle, CaretDown, ClockCounterClockwise, Code, FileCode, GearSix,
  FolderSimple, GithubLogo, GitCommit, GitPullRequest, Graph,
  GoogleLogo, MagnifyingGlass, PaperPlaneRight, Plus, ShieldCheck, Sparkle, Trash, X,
} from '@phosphor-icons/react'
import './App.css'

type NodeKind = 'root' | 'repository' | 'module' | 'directory' | 'file' | 'symbol' | 'commit' | 'pr' | 'pull_request' | 'issue' | 'person' | 'decision' | 'comment' | 'review' | string
type GraphNode = { id: string; label: string; kind: NodeKind; description: string; path?: string | null; date?: string; author?: string; content?: string; x?: number; y?: number; metadata?: Record<string, string | number | boolean | null> }
type ChatMessage = { role: 'user' | 'assistant'; text: string; sources?: string[]; citations?: EvidenceCitation[]; confidence?: string }

function getSavedAssistantSettings(): AssistantSettings {
  try {
    const saved = sessionStorage.getItem('why-assistant-settings')
    if (saved) {
      const parsed = JSON.parse(saved) as Partial<AssistantSettings>
      return { endpoint: typeof parsed.endpoint === 'string' ? parsed.endpoint : 'https://api.openai.com/v1', model: typeof parsed.model === 'string' ? parsed.model : '', apiKey: '' }
    }
  } catch { /* session storage can be disabled by browser policy */ }
  return { endpoint: 'https://api.openai.com/v1', model: '', apiKey: '' }
}

function citationHref(citation: EvidenceCitation, repository: { owner: string; repository: string; branch?: string } | null): string | undefined {
  const sourceUrl = citation.metadata.url
  if (typeof sourceUrl === 'string' && sourceUrl.startsWith('https://github.com/')) return sourceUrl
  if (citation.path && repository) {
    const branch = encodeURIComponent(repository.branch ?? 'main')
    const path = citation.path.split('/').map(encodeURIComponent).join('/')
    return `https://github.com/${encodeURIComponent(repository.owner)}/${encodeURIComponent(repository.repository)}/blob/${branch}/${path}`
  }
  return undefined
}

function changedLineCounts(before: string, after: string) {
  const original = before.split('\n')
  const draft = after.split('\n')
  let prefix = 0
  while (prefix < original.length && prefix < draft.length && original[prefix] === draft[prefix]) prefix += 1
  let suffix = 0
  while (suffix < original.length - prefix && suffix < draft.length - prefix && original[original.length - suffix - 1] === draft[draft.length - suffix - 1]) suffix += 1
  return { added: draft.length - prefix - suffix, removed: original.length - prefix - suffix }
}

const architectureNodes: GraphNode[] = [
  { id: 'repo', label: 'vercel / next.js', kind: 'root', description: 'Repository overview · illustrative preview', x: 500, y: 320 },
  { id: 'app', label: 'packages / next', kind: 'module', description: 'Framework runtime and application routing', path: 'packages/next', x: 270, y: 170 },
  { id: 'build', label: 'packages / next-swc', kind: 'module', description: 'Compiler and build integrations', path: 'packages/next-swc', x: 735, y: 170 },
  { id: 'docs', label: 'docs', kind: 'module', description: 'Product documentation and examples', path: 'docs', x: 225, y: 485 },
  { id: 'router', label: 'router.ts', kind: 'file', description: 'Request routing entry point', path: 'packages/next/src/server/lib/router-server.ts', x: 410, y: 170 },
  { id: 'render', label: 'render.tsx', kind: 'file', description: 'Server rendering pipeline', path: 'packages/next/src/server/render.tsx', x: 585, y: 170 },
  { id: 'routefn', label: 'resolveRoutes()', kind: 'symbol', description: 'Resolves a request against configured routes', path: 'packages/next/src/server/lib/router-utils/resolve-routes.ts', x: 390, y: 475 },
  { id: 'compiler', label: 'swc / compiler', kind: 'symbol', description: 'Compiler bindings and transform pipeline', path: 'packages/next-swc', x: 795, y: 335 },
  { id: 'tests', label: 'test / e2e', kind: 'module', description: 'End-to-end test coverage', path: 'test/e2e', x: 770, y: 495 },
  { id: 'config', label: 'config-shared.ts', kind: 'file', description: 'Shared configuration defaults', path: 'packages/next/src/server/config-shared.ts', x: 555, y: 475 },
]

const decisionNodes: GraphNode[] = [
  { id: 'code', label: 'config-shared.ts', kind: 'file', description: 'Current source file connected to this decision trail', path: 'packages/next/src/server/config-shared.ts', x: 260, y: 320 },
  { id: 'commit-a', label: 'Introduce shared defaults', kind: 'commit', description: 'Change introduced shared runtime configuration defaults.', date: 'Mar 18, 2023', author: 'Tim Neutkens', content: 'Adds a shared configuration layer so server and build paths read the same defaults.', x: 485, y: 165 },
  { id: 'pr-a', label: 'PR #48210', kind: 'pr', description: 'Illustrative pull request link in preview mode.', date: 'Mar 17, 2023', author: 'Tim Neutkens', content: 'Discussion associated with introducing common defaults.', x: 740, y: 165 },
  { id: 'decision-a', label: 'One config source', kind: 'decision', description: 'Keep runtime defaults centralized to reduce divergence between server paths.', date: 'Mar 17, 2023', author: 'Maintainers', content: 'Decision reconstructed from the linked change and discussion. Preview content is illustrative.', x: 510, y: 320 },
  { id: 'issue-a', label: 'Issue #47102', kind: 'issue', description: 'Illustrative issue link in preview mode.', date: 'Mar 9, 2023', author: 'Community', content: 'Reports inconsistent behavior between configuration entry points.', x: 740, y: 475 },
  { id: 'commit-b', label: 'Adjust edge-case handling', kind: 'commit', description: 'Later follow-up change; displayed as an illustrative timeline item.', date: 'Oct 6, 2024', author: 'Maintainers', content: 'A later update refines behavior. Preview mode does not assert that this supersedes the earlier decision.', x: 285, y: 485 },
  { id: 'reviewer', label: 'Reviewer', kind: 'person', description: 'Person associated with the discussion.', author: 'Contributor', x: 945, y: 320 },
]

const codePreview = [
  ['1', 'export const defaultConfig = {', 'plain'],
  ['2', '  poweredByHeader: true,', 'property'],
  ['3', '  generateEtags: true,', 'property'],
  ['4', '  compress: true,', 'property'],
  ['5', '  pageExtensions: ["tsx", "ts", "jsx", "js"],', 'property'],
  ['6', '  trailingSlash: false,', 'property'],
  ['7', '}', 'plain'],
  ['8', '', 'plain'],
  ['9', '// Shared between server and build paths.', 'comment'],
]

const graphEdges: Record<GraphMode, [string, string, string][]> = {
  architecture: [
    ['repo', 'app', 'CONTAINS'], ['repo', 'build', 'CONTAINS'], ['repo', 'docs', 'CONTAINS'],
    ['app', 'router', 'CONTAINS'], ['app', 'render', 'CONTAINS'], ['app', 'routefn', 'CONTAINS'],
    ['app', 'config', 'CONTAINS'], ['build', 'compiler', 'CONTAINS'], ['app', 'tests', 'TESTED BY'],
    ['router', 'routefn', 'CALLS'], ['render', 'config', 'READS'], ['routefn', 'config', 'READS'],
  ],
  decisions: [
    ['code', 'commit-a', 'MODIFIED BY'], ['commit-a', 'pr-a', 'LINKED TO'],
    ['pr-a', 'decision-a', 'SUPPORTS'], ['pr-a', 'issue-a', 'ADDRESSES'],
    ['decision-a', 'reviewer', 'REVIEWED BY'], ['code', 'commit-b', 'MODIFIED BY'],
  ],
}

const nodeColors: Record<string, string> = {
  root: '#d2f06c', module: '#566056', file: '#718176', symbol: '#a7b1a3',
  commit: '#8ea878', pr: '#84aaa0', issue: '#bd967c', person: '#9b91a9', decision: '#d2f06c',
}

function toDisplayNodes(mode: GraphMode, graphData: RepositoryGraphData | null): GraphNode[] {
  return graphData?.nodes.map((node: RepositoryGraphNode) => ({
    ...node,
    kind: node.kind === 'repository' ? 'root' : node.kind === 'pull_request' ? 'pr' : node.kind,
    description: node.description ?? '',
    date: typeof node.metadata.date === 'string' ? node.metadata.date : typeof node.metadata.updated_at === 'string' ? node.metadata.updated_at : undefined,
    author: typeof node.metadata.author === 'string' ? node.metadata.author : typeof node.metadata.login === 'string' ? node.metadata.login : undefined,
  })) ?? (mode === 'architecture' ? architectureNodes : decisionNodes)
}

function makeElements(mode: GraphMode, search: string, graphData: RepositoryGraphData | null, filters: string[] | null, positions: Map<string, { x: number; y: number }>): ElementDefinition[] {
  const sourceNodes = toDisplayNodes(mode, graphData)
  const normalizedSearch = search.trim().toLowerCase()
  const visibleIds = new Set(sourceNodes.filter((node) => (!filters?.length || ['root', 'repository'].includes(node.kind) || filters.includes(node.kind) || (node.kind === 'directory' && filters.includes('module')) || (node.kind === 'pull_request' && filters.includes('pr'))) && (!normalizedSearch || `${node.label} ${node.description} ${node.path ?? ''}`.toLowerCase().includes(normalizedSearch))).map((node) => node.id))
  const stablePosition = (id: string) => {
    let hash = 2166136261
    for (const character of id) hash = Math.imul(hash ^ character.charCodeAt(0), 16777619)
    const angle = ((hash >>> 0) % 6283) / 1000
    const radius = 110 + ((hash >>> 8) % 220)
    return { x: 500 + Math.cos(angle) * radius, y: 320 + Math.sin(angle) * radius }
  }
  const colors = sourceNodes.map((node) => ({
    data: { id: node.id, label: node.label, kind: node.kind, color: nodeColors[node.kind] ?? (node.kind.includes('pull') ? nodeColors.pr : nodeColors.file) },
    classes: visibleIds.has(node.id) ? '' : 'filtered-out',
    position: typeof node.x === 'number' && typeof node.y === 'number' ? { x: node.x, y: node.y } : positions.get(node.id) ?? stablePosition(node.id),
  }))
  const edges = graphData
    ? graphData.edges.map((edge) => ({ data: { id: edge.id, source: edge.source, target: edge.target, kind: edge.kind, label: edge.kind === 'SUPERSEDES' ? 'explicitly supersedes' : edge.label }, classes: visibleIds.has(edge.source) && visibleIds.has(edge.target) ? '' : 'filtered-out' }))
    : graphEdges[mode].map(([source, target, label], index) => ({ data: { id: `${source}-${target}-${index}`, source, target, label }, classes: visibleIds.has(source) && visibleIds.has(target) ? '' : 'filtered-out' }))
  return [
    ...colors,
    ...edges,
  ]
}

function GraphCanvas({ mode, query, graphData, onSelect, selectedId, filters, onZoom }: { mode: GraphMode; query: string; graphData: RepositoryGraphData | null; onSelect: (id: string) => void; selectedId: string; filters: string[] | null; onZoom: (zoom: number) => void }) {
  const host = useRef<HTMLDivElement>(null)
  const graph = useRef<Core | null>(null)
  const positions = useRef(new Map<string, { x: number; y: number }>())
  const didInitialLayout = useRef(false)
  const propsRef = useRef({ mode, query, graphData, filters, onSelect, onZoom })
  useEffect(() => { propsRef.current = { mode, query, graphData, filters, onSelect, onZoom } }, [mode, query, graphData, filters, onSelect, onZoom])
  const [graphInstanceVersion, setGraphInstanceVersion] = useState(0)
  const [loadError, setLoadError] = useState(false)

  useEffect(() => {
    if (!host.current) return
    const container = host.current
    const positionCache = positions.current
    let instance: Core | null = null
    let cancelled = false
    const fit = () => graph.current?.fit(undefined, 64)
    const zoomIn = () => { const instance = graph.current; if (instance) instance.zoom({ level: Math.min(instance.zoom() * 1.2, 2.1), renderedPosition: { x: instance.width() / 2, y: instance.height() / 2 } }) }
    const zoomOut = () => { const instance = graph.current; if (instance) instance.zoom({ level: Math.max(instance.zoom() / 1.2, 0.35), renderedPosition: { x: instance.width() / 2, y: instance.height() / 2 } }) }
    void import('cytoscape').then(({ default: cytoscape }) => {
      if (cancelled || !host.current) return
      const current = propsRef.current
      instance = cytoscape({
        container,
        elements: makeElements(current.mode, current.query, current.graphData, current.filters, positions.current),
        layout: { name: 'preset', fit: true, padding: 64 },
        minZoom: 0.35, maxZoom: 2.1,
        style: [
          { selector: 'node', style: { 'background-color': 'data(color)', 'label': 'data(label)', 'color': '#e6e9df', 'font-size': '11px', 'font-family': 'Segoe UI, sans-serif', 'font-weight': 500, 'text-wrap': 'wrap', 'text-max-width': '120px', 'text-valign': 'bottom', 'text-margin-y': 11, 'text-outline-width': 2, 'text-outline-color': '#111410', 'width': 25, 'height': 25, 'border-width': 1, 'border-color': '#111410', 'overlay-opacity': 0, 'transition-property': 'width height border-width', 'transition-duration': 180 } },
          { selector: 'node[kind = "root"], node[kind = "decision"]', style: { width: 36, height: 36, 'border-width': 2, 'border-color': '#e7f5bf', 'font-size': '12px' } },
          { selector: 'node[kind = "module"], node[kind = "directory"]', style: { shape: 'round-rectangle', width: 30, height: 30 } },
          { selector: 'node[kind = "pr"], node[kind = "pull_request"]', style: { 'border-width': 2, 'border-color': '#84aaa0' } },
          { selector: 'node[kind = "commit"]', style: { shape: 'diamond' } },
          { selector: 'node[kind = "issue"]', style: { shape: 'round-tag' } },
          { selector: '.filtered-out', style: { display: 'none' } },
          { selector: 'node:selected', style: { 'border-width': 3, 'border-color': '#d2f06c', 'width': 34, 'height': 34, 'z-index': 999 } },
          { selector: 'edge', style: { 'curve-style': 'bezier', 'line-color': '#40473d', 'target-arrow-color': '#737d6b', 'target-arrow-shape': 'triangle', 'arrow-scale': 0.72, 'width': 1.2, 'label': 'data(label)', 'font-size': '8px', 'color': '#9ca496', 'text-background-color': '#111410', 'text-background-opacity': 0.92, 'text-background-padding': '3px', 'text-rotation': 'autorotate', 'opacity': 0.85 } },
          { selector: 'edge[kind = "SUPERSEDES"]', style: { 'line-color': '#d2f06c', 'target-arrow-color': '#d2f06c', width: 2, color: '#d2f06c', 'font-weight': 700, opacity: 1 } },
          { selector: 'edge:selected', style: { 'line-color': '#d2f06c', 'target-arrow-color': '#d2f06c', width: 2 } },
        ],
      })
      graph.current = instance
      didInitialLayout.current = !current.graphData
      setGraphInstanceVersion((version) => version + 1)
      current.onZoom(Math.round(instance.zoom() * 100))
      instance.on('zoom', () => current.onZoom(Math.round((instance?.zoom() ?? 1) * 100)))
      instance.on('tap', 'node', (event) => propsRef.current.onSelect(event.target.id()))
      container.addEventListener('fitgraph', fit)
      container.addEventListener('zoom-in', zoomIn)
      container.addEventListener('zoom-out', zoomOut)
    }).catch(() => { if (!cancelled) setLoadError(true) })
    return () => {
      cancelled = true
      container.removeEventListener('fitgraph', fit)
      container.removeEventListener('zoom-in', zoomIn)
      container.removeEventListener('zoom-out', zoomOut)
      instance?.nodes().forEach((node) => { if (!node.empty()) positionCache.set(node.id(), node.position()) })
      instance?.destroy()
      if (graph.current === instance) graph.current = null
    }
  }, [])

  useEffect(() => {
    const instance = graph.current
    if (!instance) return
    const positionCacheForLayout = positions.current
    const desired = makeElements(mode, query, graphData, filters, positions.current)
    const desiredById = new Map(desired.map((element) => [String(element.data?.id), element]))
    let addedNodeCount = 0
    instance.batch(() => {
      instance.edges().forEach((edge) => { if (!desiredById.has(edge.id())) edge.remove() })
      instance.nodes().forEach((node) => {
        if (!desiredById.has(node.id())) {
          positions.current.set(node.id(), node.position())
          node.remove()
        }
      })
      for (const element of desired) {
        const id = String(element.data?.id ?? '')
        if (!id) continue
        const existing = instance.getElementById(id)
        if (existing.length) {
          existing.data(element.data ?? {})
          existing.classes(element.classes ?? '')
        } else {
          instance.add(element)
          if (element.data && !('source' in element.data)) addedNodeCount += 1
        }
      }
    })
    if (graphData && addedNodeCount && !didInitialLayout.current) {
      didInitialLayout.current = true
      instance.layout({ name: 'cose', fit: true, padding: 64, animate: false, randomize: false }).run()
      instance.nodes().forEach((node) => { positionCacheForLayout.set(node.id(), node.position()) })
    }
  }, [mode, query, graphData, filters, graphInstanceVersion])

  useEffect(() => {
    if (!graph.current) return
    graph.current.nodes().unselect()
    if (selectedId && graph.current.getElementById(selectedId).length) graph.current.getElementById(selectedId).select()
  }, [selectedId, graphInstanceVersion])

  return <div className="graph-canvas" ref={host} aria-label={`${mode === 'architecture' ? 'Code structure' : 'Decision history'} graph`}>{loadError && <div className="graph-error" role="status">The graph view could not load. Refresh the workspace to try again.</div>}</div>
}

function App() {
  const [mode, setMode] = useState<GraphMode>('architecture')
  const [query, setQuery] = useState('')
  const [graphFilters, setGraphFilters] = useState<string[] | null>(null)
  const [graphZoom, setGraphZoom] = useState(100)
  const [showGraphFilters, setShowGraphFilters] = useState(false)
  const [showInspector, setShowInspector] = useState(true)
  const [selectedId, setSelectedId] = useState('routefn')
  const [repoUrl, setRepoUrl] = useState('')
  const [storageConsent, setStorageConsent] = useState(false)
  const [workspace, setWorkspace] = useState(false)
  const [isIndexing, setIsIndexing] = useState(false)
  const [indexProgress, setIndexProgress] = useState({ progress: 0, message: 'Preparing repository analysis' })
  const [apiHealth, setApiHealth] = useState<{ ready: boolean; message: string } | null>(null)
  const [activeRepository, setActiveRepository] = useState<{ owner: string; repository: string; fullName: string; branch?: string } | null>(null)
  const [savedRepositories, setSavedRepositories] = useState<RepositorySummary[]>([])
  const [githubLogin, setGithubLogin] = useState<string | null>(null)
  const [githubOAuthConfigured, setGithubOAuthConfigured] = useState(false)
  const [googleOAuthConfigured, setGoogleOAuthConfigured] = useState(false)
  const [emailAuthConfigured, setEmailAuthConfigured] = useState(false)
  const [showAuth, setShowAuth] = useState(false)
  const [authEmail, setAuthEmail] = useState('')
  const [authPassword, setAuthPassword] = useState('')
  const [authPasswordConfirm, setAuthPasswordConfirm] = useState('')
  const [authCode, setAuthCode] = useState('')
  const [authMode, setAuthMode] = useState<'signin' | 'signup'>('signin')
  const [authStep, setAuthStep] = useState<'credentials' | 'signup-code' | 'two-factor'>('credentials')
  const [authBusy, setAuthBusy] = useState(false)
  const [authMessage, setAuthMessage] = useState('')
  const [showAccountSettings, setShowAccountSettings] = useState(false)
  const [accountInfo, setAccountInfo] = useState<AccountSettings | null>(null)
  const [accountPassword, setAccountPassword] = useState('')
  const [accountMessage, setAccountMessage] = useState('')
  const [accountBusy, setAccountBusy] = useState(false)
  const [twoFactorEnrollment, setTwoFactorEnrollment] = useState(false)
  const [twoFactorCode, setTwoFactorCode] = useState('')
  const [activeIsSaved, setActiveIsSaved] = useState(false)
  const [liveGraph, setLiveGraph] = useState<RepositoryGraphData | null>(null)
  const repositoryOwner = activeRepository?.owner
  const repositoryName = activeRepository?.repository
  const [graphLoading, setGraphLoading] = useState(false)
  const [currentSource, setCurrentSource] = useState<RepositorySource | null>(null)
  const [sourceError, setSourceError] = useState('')
  const [editorPath, setEditorPath] = useState<string | null>(null)
  const [editorDraft, setEditorDraft] = useState<{ path: string; content: string } | null>(null)
  const [editorScrollTop, setEditorScrollTop] = useState(0)
  const [chat, setChat] = useState<ChatMessage[]>([{ role: 'assistant', text: 'Ask about this codebase. I’ll trace the answer back to repository evidence and call out when the history can’t support a conclusion.' }])
  const [question, setQuestion] = useState('')
  const [assistantSettings, setAssistantSettings] = useState<AssistantSettings>(getSavedAssistantSettings)
  const [showAssistantSettings, setShowAssistantSettings] = useState(false)
  const [shareWithProvider, setShareWithProvider] = useState(false)
  const [assistantBusy, setAssistantBusy] = useState(false)
  const [providerTestBusy, setProviderTestBusy] = useState(false)
  const [providerTestMessage, setProviderTestMessage] = useState('')
  const [activeSource, setActiveSource] = useState('code')
  const [notice, setNotice] = useState(() => {
    const authResult = new URLSearchParams(window.location.search).get('auth')
    if (authResult === 'unavailable') return 'GitHub sign-in is not configured on the API yet.'
    if (authResult && authResult !== 'success') return 'GitHub sign-in did not complete. Please try again.'
    return ''
  })
  const nodes = toDisplayNodes(mode, liveGraph)
  const selected = nodes.find((node) => node.id === selectedId) ?? nodes[0]
  const connectedNodes = liveGraph && selected
    ? liveGraph.edges.filter((edge) => edge.source === selected.id || edge.target === selected.id)
      .map((edge) => nodes.find((node) => node.id === (edge.source === selected.id ? edge.target : edge.source)))
      .filter((node): node is GraphNode => Boolean(node))
      .filter((node, index, list) => list.findIndex((candidate) => candidate.id === node.id) === index)
    : []
  const supersededBy = activeRepository && liveGraph && selected
    ? liveGraph.edges.filter((edge) => edge.kind === 'SUPERSEDES' && edge.target === selected.id)
      .map((edge) => nodes.find((node) => node.id === edge.source))
      .filter((node): node is GraphNode => Boolean(node))
    : []
  const supersedes = activeRepository && liveGraph && selected
    ? liveGraph.edges.filter((edge) => edge.kind === 'SUPERSEDES' && edge.source === selected.id)
      .map((edge) => nodes.find((node) => node.id === edge.target))
      .filter((node): node is GraphNode => Boolean(node))
    : []
  const selectNode = useMemo(() => (id: string) => {
    setSelectedId(id)
    setCurrentSource(null)
    setSourceError('')
    setEditorPath(null)
    setEditorDraft(null)
  }, [])

  useEffect(() => {
    let active = true
    void startBrowserSession().then(async (session) => {
      if (active) {
        setGithubLogin(session.authenticated ? session.login ?? 'GitHub user' : null)
        setGithubOAuthConfigured(Boolean(session.github_oauth_configured ?? session.oauth_configured))
        setGoogleOAuthConfigured(Boolean(session.google_oauth_configured))
        setEmailAuthConfigured(Boolean(session.email_auth_configured))
      }
      const health = await checkApiHealth()
      if (active) setApiHealth({ ready: health.status === 'ready' && health.neo4j_configured, message: health.message ?? '' })
      try {
        const repositories = await getRepositories()
        if (active) setSavedRepositories(repositories)
      } catch { /* keep the landing page usable if Aura is not configured */ }
    }).catch((error: unknown) => {
      if (active) setApiHealth({ ready: false, message: error instanceof Error ? error.message : 'The API is unavailable.' })
    })
    return () => { active = false }
  }, [])

  useEffect(() => {
    const authResult = new URLSearchParams(window.location.search).get('auth')
    if (!authResult) return
    if (authResult === 'success') {
      void startBrowserSession().then((session) => {
        setGithubLogin(session.authenticated ? session.login ?? 'GitHub user' : null)
        setNotice(session.authenticated ? `Signed in as ${session.login}. You can now choose which indexes to save.` : 'Sign-in could not be verified. Please try again.')
      })
    }
    window.history.replaceState({}, '', window.location.pathname)
  }, [])

  useEffect(() => {
    const cleanupTemporaryWorkspace = () => {
      try {
        if (sessionStorage.getItem('why-oauth-redirect') === 'true') {
          sessionStorage.removeItem('why-oauth-redirect')
          return
        }
      } catch { /* best-effort session storage */ }
      const payload = new Blob([], { type: 'text/plain' })
      if (!navigator.sendBeacon('/api/session/cleanup', payload)) {
        void fetch('/api/session/cleanup', {
          method: 'POST', credentials: 'include', keepalive: true,
          headers: { 'Content-Type': 'text/plain' }, body: '',
        })
      }
    }
    window.addEventListener('pagehide', cleanupTemporaryWorkspace)
    return () => window.removeEventListener('pagehide', cleanupTemporaryWorkspace)
  }, [])

  useEffect(() => {
    try { sessionStorage.setItem('why-assistant-settings', JSON.stringify({ endpoint: assistantSettings.endpoint, model: assistantSettings.model })) } catch { /* keep settings in memory if storage is unavailable */ }
  }, [assistantSettings])

  useEffect(() => {
    if (!repositoryOwner || !repositoryName) return
    let active = true
    void getRepositoryGraph(repositoryOwner, repositoryName, mode).then((graph) => {
      if (!active) return
      setLiveGraph(graph)
      const branch = graph.repository.default_branch
      if (typeof branch === 'string') setActiveRepository((current) => current ? { ...current, branch } : current)
      setCurrentSource((source) => source && graph.nodes.some((node) => node.kind === 'file' && node.path === source.path) ? source : null)
      setSourceError('')
      const firstNode = graph.nodes.find((node) => node.kind === 'file') ?? graph.nodes[0]
      if (firstNode) setSelectedId((current) => graph.nodes.some((node) => node.id === current) ? current : firstNode.id)
    }).catch((error: unknown) => {
      if (active) setNotice(error instanceof Error ? error.message : 'The repository graph could not be loaded.')
    }).finally(() => { if (active) setGraphLoading(false) })
    return () => { active = false }
  }, [repositoryOwner, repositoryName, mode])

  useEffect(() => {
    if (!repositoryOwner || !repositoryName || !selected?.path || selected.kind !== 'file') return
    let active = true
    void getRepositoryFile(repositoryOwner, repositoryName, selected.path).then((source) => {
      if (active) setCurrentSource(source)
    }).catch((error: unknown) => {
      if (active) {
        setCurrentSource(null)
        setSourceError(error instanceof Error ? error.message : 'This file could not be loaded.')
      }
    })
    return () => { active = false }
  }, [repositoryOwner, repositoryName, selected?.id, selected?.kind, selected?.path])

  const handleSubmitRepo = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const normalized = repoUrl.trim()
    if (!normalized) { setNotice('Paste a public GitHub repository URL to continue.'); return }
    if (!storageConsent) { setNotice('Please confirm that the selected public repository history will be stored in the connected Neo4j database.'); return }
    setNotice('')
    setIsIndexing(true)
    setIndexProgress({ progress: 2, message: 'Starting secure repository import' })
    try {
      const { job_id: jobId } = await startRepositoryAnalysis(normalized)
      let completedJob: Awaited<ReturnType<typeof getAnalysisJob>> | null = null
      for (let attempt = 0; attempt < 240; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1_500))
        const job = await getAnalysisJob(jobId)
        setIndexProgress({ progress: job.progress, message: job.message })
        if (job.status === 'complete') { completedJob = job; break }
        if (job.status === 'failed') throw new RepositoryApiError(job.message, job.error_code ?? 'indexing_failed', 422)
      }
      if (!completedJob?.repository) throw new RepositoryApiError('Repository indexing took too long. Try again or choose a smaller repository.', 'indexing_timeout', 408)
      const [owner, repository] = completedJob.repository.split('/')
      if (!owner || !repository) throw new RepositoryApiError('GitHub returned an invalid repository name.', 'invalid_repository', 502)
      setActiveRepository({ owner, repository, fullName: completedJob.repository })
      setActiveIsSaved(false)
      void getRepositories().then(setSavedRepositories).catch(() => { /* graph load still works if the list refresh fails */ })
      setChat([{ role: 'assistant', text: 'Ask about this codebase. I’ll use connected commits, discussions, and source files, cite indexed records, and say when the available history cannot support an answer.' }])
      setShareWithProvider(false)
      setLiveGraph({ repository: {}, nodes: [], edges: [], coverage: {} })
      setGraphLoading(true)
      setCurrentSource(null)
      setSourceError('')
      setMode('architecture')
      setWorkspace(true)
      setSelectedId('')
      setNotice('')
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Repository indexing failed. Please try again.')
    } finally {
      setIsIndexing(false)
    }
  }

  const handleDemo = () => {
    setActiveRepository(null)
    setActiveIsSaved(false)
    setLiveGraph(null)
    setGraphLoading(false)
    setCurrentSource(null)
    setSourceError('')
    setMode('architecture')
    setShareWithProvider(false)
    setChat([{ role: 'assistant', text: 'This is an illustrative sample workspace. Ask a question to see how the evidence flow will look.' }])
    setSelectedId('routefn')
    setWorkspace(true)
    setNotice('You are viewing an illustrative sample workspace. No repository data has been fetched yet.')
  }

  const leaveCurrentRepository = () => {
    const leaving = activeRepository
    if (leaving && !activeIsSaved) {
      void deleteRepository(leaving.owner, leaving.repository)
        .then(() => setSavedRepositories((repositories) => repositories.filter((repository) => repository.full_name !== leaving.fullName)))
        .catch(() => setNotice('This temporary graph could not be deleted immediately. Automatic cleanup will remove it within two hours.'))
    }
    setWorkspace(false)
    setActiveRepository(null)
    setLiveGraph(null)
    setCurrentSource(null)
    setShareWithProvider(false)
    setNotice('')
  }

  const openSavedRepository = (repository: RepositorySummary) => {
    setActiveRepository({ owner: repository.owner, repository: repository.repository, fullName: repository.full_name, branch: repository.default_branch })
    setActiveIsSaved(repository.is_saved)
    setLiveGraph({ repository: {}, nodes: [], edges: [], coverage: {} })
    setGraphLoading(true)
    setCurrentSource(null)
    setSourceError('')
    setMode('architecture')
    setSelectedId('')
    setChat([{ role: 'assistant', text: 'Ask about this codebase. I’ll use connected commits, discussions, and source files, cite indexed records, and say when the available history cannot support an answer.' }])
    setShareWithProvider(false)
    setWorkspace(true)
  }

  const openAuthDialog = () => {
    setAuthMode('signin')
    setAuthStep('credentials')
    setAuthPassword('')
    setAuthPasswordConfirm('')
    setAuthCode('')
    setAuthMessage('')
    setShowAuth(true)
  }

  const completeEmailSignIn = (login: string) => {
    setGithubLogin(login)
    setShowAuth(false)
    setNotice(`Signed in as ${login}. You can now choose which indexes to save.`)
    setAuthMessage('')
    void getRepositories().then(setSavedRepositories).catch(() => { /* sign-in remains available without saved-index refresh */ })
  }

  const handleEmailSignup = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (authPassword !== authPasswordConfirm) { setAuthMessage('Passwords do not match.'); return }
    setAuthBusy(true)
    try {
      const result = await registerEmailAccount(authEmail.trim(), authPassword)
      setAuthStep('signup-code')
      setAuthCode('')
      setAuthMessage(result.message)
    } catch (error) {
      setAuthMessage(error instanceof Error ? error.message : 'Email signup could not be started.')
    } finally { setAuthBusy(false) }
  }

  const handleEmailLogin = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setAuthBusy(true)
    try {
      const session = await loginWithEmail(authEmail.trim(), authPassword)
      if (session.requires_2fa) {
        setAuthStep('two-factor')
        setAuthCode('')
        setAuthMessage(`A sign-in code was sent to ${session.login}.`)
      } else {
        completeEmailSignIn(session.login)
      }
    } catch (error) {
      setAuthMessage(error instanceof Error ? error.message : 'Email sign-in failed.')
    } finally { setAuthBusy(false) }
  }

  const handleVerifyEmailCode = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setAuthBusy(true)
    try {
      const session = authStep === 'signup-code'
        ? await verifyEmailSignup(authEmail.trim(), authCode.trim())
        : await verifyEmailTwoFactor(authEmail.trim(), authCode.trim())
      completeEmailSignIn(session.login)
    } catch (error) {
      setAuthMessage(error instanceof Error ? error.message : 'That code could not be verified.')
    } finally { setAuthBusy(false) }
  }

  const openAccountSettings = async () => {
    setAccountMessage('Loading account settings…')
    setAccountPassword('')
    setShowAccountSettings(true)
    try {
      setAccountInfo(await getAccountSettings())
      setAccountMessage('')
    } catch (error) {
      setAccountMessage(error instanceof Error ? error.message : 'Account settings could not be loaded.')
    }
  }

  const handleToggleTwoFactor = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!accountInfo) return
    setAccountBusy(true)
    setAccountMessage('')
    try {
      const result = await updateTwoFactor(!accountInfo.two_factor_enabled, accountPassword)
      setAccountPassword('')
      if (result.enrollment_required) {
        setTwoFactorEnrollment(true)
        setTwoFactorCode('')
        setAccountMessage(`A setup code was sent to ${accountInfo.email}. Enter it below to enable two-step verification.`)
      } else {
        setAccountInfo({ ...accountInfo, two_factor_enabled: result.two_factor_enabled })
        setTwoFactorEnrollment(false)
        setAccountMessage('Email two-step verification is disabled.')
      }
    } catch (error) {
      setAccountMessage(error instanceof Error ? error.message : 'The security setting could not be updated.')
    } finally { setAccountBusy(false) }
  }

  const handleConfirmTwoFactorEnrollment = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setAccountBusy(true)
    try {
      await confirmTwoFactorEnrollment(twoFactorCode)
      setAccountInfo((current) => current ? { ...current, two_factor_enabled: true } : current)
      setTwoFactorEnrollment(false)
      setTwoFactorCode('')
      setAccountMessage('Email two-step verification is enabled.')
    } catch (error) {
      setAccountMessage(error instanceof Error ? error.message : 'The setup code could not be verified.')
    } finally { setAccountBusy(false) }
  }

  const handleSaveIndex = async () => {
    if (!activeRepository) return
    if (!githubLogin) { openAuthDialog(); return }
    try {
      await saveRepository(activeRepository.owner, activeRepository.repository)
      setActiveIsSaved(true)
      setSavedRepositories(await getRepositories())
      setNotice(`${activeRepository.fullName} is saved to your account.`)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'This index could not be saved.')
    }
  }

  const handleLogout = async () => {
    try {
      await logout()
      setGithubLogin(null)
      setShowAccountSettings(false)
      setActiveIsSaved(false)
      setSavedRepositories([])
      setWorkspace(false)
      setActiveRepository(null)
      setLiveGraph(null)
      setCurrentSource(null)
      setNotice('Signed out. Your saved indexes remain private to your account.')
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Sign-out failed.')
    }
  }

  const handleTestProvider = async () => {
    setProviderTestBusy(true)
    setProviderTestMessage('Testing connection without repository data…')
    try {
      const result = await testModelConnection(assistantSettings)
      setProviderTestMessage(result)
    } catch (error) {
      setProviderTestMessage(error instanceof Error ? error.message : 'The provider connection could not be tested.')
    } finally { setProviderTestBusy(false) }
  }

  const submitQuestion = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const text = question.trim()
    if (!text || assistantBusy) return
    if (activeRepository) {
      if (!shareWithProvider) { setNotice('Confirm before sending repository evidence to your selected model provider.'); return }
      if (!activeRepository || !selected) return
      setQuestion('')
      setChat((current) => [...current, { role: 'user', text }])
      setAssistantBusy(true)
      try {
        const evidenceGraph = await getRepositoryGraph(activeRepository.owner, activeRepository.repository, 'decisions')
        const relevantSource = currentSource && currentSource.path === selected.path
          ? { ...currentSource, content: editorDraft?.path === currentSource.path ? editorDraft.content : currentSource.content }
          : null
        const answer = await askWithEvidence(assistantSettings, text, evidenceGraph, selected.id, relevantSource)
        setChat((current) => [...current, { role: 'assistant', text: answer.answer, citations: answer.citations, confidence: answer.confidence }])
      } catch (error) {
        setChat((current) => [...current, { role: 'assistant', text: error instanceof Error ? error.message : 'The assistant could not complete this request.' }])
      } finally { setAssistantBusy(false) }
      return
    }
    setChat((current) => [...current, { role: 'user', text }, { role: 'assistant', text: 'This preview uses illustrative repository content. Once a repository is indexed, I’ll answer from linked code, commits, and discussions, with the source and date attached to each finding.', sources: ['Source citations will appear after indexing'] }])
    setQuestion('')
  }

  const openGitHub = (path?: string) => {
    if (!path) return
    const owner = activeRepository?.owner ?? 'vercel'
    const repository = activeRepository?.repository ?? 'next.js'
    const ref = activeRepository?.branch ?? 'main'
    const safePath = path.split('/').map(encodeURIComponent).join('/')
    const url = `https://github.com/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/blob/${ref}/${safePath}`
    window.open(url, '_blank', 'noopener,noreferrer')
  }

  const switchGraphMode = (nextMode: GraphMode) => {
    if (nextMode === mode) return
    if (activeRepository) setGraphLoading(true)
    setMode(nextMode)
  }

  const removeIndexedRepository = async () => {
    if (!activeRepository || !window.confirm(`Remove the stored graph for ${activeRepository.fullName} from Neo4j?`)) return
    try {
      await deleteRepository(activeRepository.owner, activeRepository.repository)
      setSavedRepositories((repositories) => repositories.filter((repository) => repository.full_name !== activeRepository.fullName))
      setWorkspace(false)
      setActiveRepository(null)
      setLiveGraph(null)
      setCurrentSource(null)
      setNotice('Repository graph removed from the connected Neo4j database.')
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'The repository graph could not be removed.')
    }
  }

  const sourceLines = activeRepository && selected?.path && selected.kind === 'file'
    ? currentSource?.path === selected.path ? currentSource.content.split('\n').slice(0, 9).map((line, index) => [String(index + 1), line, 'plain'] as string[]) : []
    : codePreview
  const previewPath = activeRepository
    ? selected?.kind === 'file' && typeof selected.path === 'string' ? selected.path : undefined
    : 'packages/next/src/server/config-shared.ts'
  const previewName = previewPath?.split('/').pop() ?? 'Select a file'
  const editorOriginal = currentSource && currentSource.path === selected?.path ? currentSource.content : ''
  const selectedDraft = editorDraft && editorDraft.path === selected?.path ? editorDraft.content : null
  const editorOpen = Boolean(selected?.path && editorPath === selected.path)
  const editorText = selectedDraft ?? editorOriginal
  const editorChanges = useMemo(() => changedLineCounts(editorOriginal, editorText), [editorOriginal, editorText])
  const editorDirty = editorText !== editorOriginal
  const openSourceEditor = () => {
    if (!currentSource || currentSource.path !== selected?.path) {
      setNotice('Wait for the selected source file to finish loading, then open the editor.')
      return
    }
    setEditorDraft((draft) => draft?.path === currentSource.path ? draft : { path: currentSource.path, content: currentSource.content })
    setEditorPath(currentSource.path)
  }

  return (
    <main className="app-shell">
      {!workspace ? <section className="landing">
        <header className="landing-nav">
          <a className="brand" href="#top" aria-label="Why the Code home"><img className="brand-symbol" src="/brand-mark.svg" alt="" /><span>why<span className="brand-period">.</span></span></a>
          <div className="nav-right"><span className="nav-status"><span className={`status-dot ${apiHealth?.ready ? '' : 'muted-dot'}`} /> {apiHealth?.ready ? 'Neo4j graph ready' : 'Evidence-first analysis'}</span>{githubLogin ? <button className="nav-link" onClick={() => void openAccountSettings()}>@{githubLogin} · Account</button> : <button className="nav-link" onClick={openAuthDialog}>Sign in</button>}<button className="nav-link" onClick={handleDemo}>Explore sample <ArrowRight size={15} /></button></div>
        </header>
        <div className="landing-grid" id="top">
          <section className="hero-copy">
            <div className="eyebrow"><span className="eyebrow-line" /> REPOSITORY INTELLIGENCE</div>
            <h1>Code tells you <em>what.</em><br />History tells you <em>why.</em></h1>
            <p className="hero-lede">Trace the reasoning behind a codebase. Connect files to the commits, conversations, and decisions that shaped them.</p>
            <form className="repo-form" onSubmit={handleSubmitRepo}>
              <GithubLogo size={19} weight="fill" />
              <input aria-label="Public GitHub repository URL" value={repoUrl} onChange={(event) => setRepoUrl(event.target.value)} placeholder="github.com/owner/repository" />
              <button type="submit" disabled={isIndexing}>{isIndexing ? `Indexing ${indexProgress.progress}%` : <>Analyze repo <ArrowRight size={16} /></>}</button>
            </form>
            <label className="storage-consent"><input type="checkbox" checked={storageConsent} onChange={(event) => setStorageConsent(event.target.checked)} /><span>I agree that this app reads a bounded subset of public files and GitHub history, then temporarily stores file paths, relationships, and history records in its configured Neo4j database. Source file contents are not stored there. You can explicitly save an index to your account; otherwise it is deleted when you leave or after two hours of inactivity.</span></label>
            <div className="form-caption"><ShieldCheck size={15} /> Read-only · No code execution · Saving requires sign-in and your explicit action</div>
            {isIndexing && <div className="index-progress" role="status"><div className="progress-track"><span style={{ width: `${indexProgress.progress}%` }} /></div><span>{indexProgress.message}</span></div>}
            <button className="folder-entry" onClick={() => setNotice('Folder import will be available in the native app. The web version analyzes public GitHub repositories.')}> <FolderSimple size={17} /> Working with a local clone? <span>Import folder in the desktop app <ArrowRight size={14} /></span></button>
            {savedRepositories.length > 0 && <section className="saved-repositories" aria-label="Saved and temporary repository indexes"><div className="saved-repositories-head"><b>YOUR REPOSITORY INDEXES</b><span>SAVED: {savedRepositories.filter((item) => item.is_saved).length} · TEMPORARY: {savedRepositories.filter((item) => !item.is_saved).length}</span></div>{savedRepositories.slice(0, 8).map((repository) => <button className="saved-repository" key={repository.full_name} onClick={() => openSavedRepository(repository)}><span className="saved-repository-icon"><GithubLogo size={14} weight="fill" /></span><span><b>{repository.full_name}</b><small>{repository.is_saved ? 'Saved to your account' : 'Temporary · expires after inactivity'} · {repository.default_branch}</small></span><ArrowRight size={14} /></button>)}</section>}
            {notice && !workspace && <p className="notice" role="status">{notice}</p>}
            <div className="hero-proof"><div className="proof-avatars"><span>G</span><span>↗</span><span>∷</span></div><span>For the code we inherit<br /><strong>and the context we lose.</strong></span></div>
          </section>
          <section className="hero-visual" aria-label="Illustration of repository history connected to code">
            <div className="visual-topline"><span>01 / SYSTEM TRACE</span><span className="visual-live"><span className="status-dot" /> GRAPH PREVIEW</span></div>
            <div className="visual-grid" />
            <svg className="hero-lines" viewBox="0 0 580 475" aria-hidden="true">
              <path d="M110 205 C175 180 190 125 263 110 S380 106 436 158" />
              <path d="M110 205 C175 235 205 320 278 333 S365 315 436 270" />
              <path d="M263 110 C278 170 296 255 278 333" />
              <path d="M436 158 C475 185 475 234 436 270" />
              <path d="M278 333 C315 380 378 388 433 365" />
              <path d="M110 205 C84 263 101 316 145 344" />
            </svg>
            <div className="visual-node node-code"><span className="node-icon"><Code size={15} /></span><span><b>retry-policy.ts</b><small>current code</small></span><span className="node-pulse" /></div>
            <div className="visual-node node-commit"><span className="node-icon commit-icon"><GitCommit size={15} /></span><span><b>Set retry ceiling</b><small>commit · 2 years ago</small></span></div>
            <div className="visual-node node-pr"><span className="node-icon pr-icon"><GitPullRequest size={15} /></span><span><b>PR discussion</b><small>maintainer review</small></span></div>
            <div className="visual-node node-issue"><span className="node-icon issue-icon"><BookOpenText size={15} /></span><span><b>Incident #284</b><small>triggered this change</small></span></div>
            <div className="visual-node node-decision"><span className="decision-dot" /><span><b>Why three attempts?</b><small>decision reconstructed</small></span></div>
            <div className="visual-node node-owner"><span className="owner-avatar">MK</span><span><b>M. Kim</b><small>decision owner</small></span></div>
            <div className="visual-foot"><span><span className="legend-dot code-dot" /> CODE</span><span><span className="legend-dot history-dot" /> HISTORY</span><span><span className="legend-dot decision-legend" /> DECISION</span></div>
            <div className="visual-index">GRAPH / 001 — 006</div>
          </section>
        </div>
        <section className="value-strip">
          <div><span className="strip-index">01</span><span><b>Follow the evidence</b><small>Every answer points to its source.</small></span></div>
          <div><span className="strip-index">02</span><span><b>See the full timeline</b><small>Know when a decision changed.</small></span></div>
          <div><span className="strip-index">03</span><span><b>Keep control</b><small>Read-only, transparent by default.</small></span></div>
        </section>
        <footer className="landing-footer"><span>WHY THE CODE IS LIKE THIS</span><span>BUILDING CONTEXT THAT STAYS</span></footer>
      </section> : <section className="workspace">
        <header className="workspace-topbar">
          <a className="brand" href="#top" onClick={(event) => { event.preventDefault(); leaveCurrentRepository() }} aria-label="Why the Code home"><img className="brand-symbol" src="/brand-mark.svg" alt="" /><span>why<span className="brand-period">.</span></span></a>
          <div className="repo-crumb"><span className="crumb-slash">/</span><GithubLogo size={15} weight="fill" /><b>{activeRepository?.fullName ?? 'vercel / next.js'}</b><span className="sample-tag">{activeRepository ? 'INDEXED' : 'SAMPLE'}</span><CaretDown size={13} /></div>
          <div className="topbar-right"><span className="indexed-status"><span className={`status-dot ${activeRepository ? '' : 'muted-dot'}`} /> {activeRepository ? activeIsSaved ? 'Saved index' : 'Temporary index' : 'Preview data only'}</span>{activeRepository && !activeIsSaved && <button className="topbar-save" onClick={() => void handleSaveIndex()}>{githubLogin ? 'Save index' : 'Sign in to save'}</button>}{githubLogin && <button className="topbar-account" onClick={() => void openAccountSettings()} title="Account settings">@{githubLogin}</button>}{activeRepository && <button className="topbar-icon" aria-label="Remove repository index" title="Remove repository graph" onClick={() => void removeIndexedRepository()}><Trash size={16} /></button>}<button className="topbar-new" onClick={leaveCurrentRepository}><Plus size={16} /> New repo</button></div>
        </header>
        <div className="workspace-body">
          <aside className="left-rail">
            <button className={`rail-item ${mode === 'architecture' ? 'selected' : ''}`} aria-label="Code map" title="Code map" onClick={() => switchGraphMode('architecture')}><Graph size={19} weight="fill" /></button>
            <button className="rail-item" aria-label="Code browser" onClick={() => setActiveSource('code')}><FileCode size={19} /></button>
            <button className="rail-item" aria-label="Decision timeline" onClick={() => setMode('decisions')}><ClockCounterClockwise size={19} /></button>
            <span className="rail-spacer" />
            <button className="rail-item" aria-label="Privacy details" title="Privacy details" onClick={() => setNotice('Privacy: repository source is fetched for analysis and previews, then kept in short-lived server memory only. Graph metadata is temporary in Neo4j unless you explicitly save the index.') }><ShieldCheck size={19} /></button>
            <button className="rail-item" aria-label={showInspector ? 'Hide details panel' : 'Show details panel'} title={showInspector ? 'Hide details panel' : 'Show details panel'} onClick={() => setShowInspector((open) => !open)}><FileCode size={19} /></button>
            <div className="rail-avatar">W</div>
          </aside>
          <section className="graph-area">
            <div className="workspace-heading">
              <div><div className="workspace-kicker"><span>REPOSITORY MAP</span><span className="kicker-divider">/</span><span>{mode === 'architecture' ? 'CODE STRUCTURE' : 'DECISION HISTORY'}</span></div><h2>{mode === 'architecture' ? 'How the pieces connect' : 'How the decisions evolved'}</h2></div>
              <button className="graph-action" aria-label="Fit graph to screen" onClick={() => document.querySelector('.graph-canvas')?.dispatchEvent(new Event('fitgraph'))}><ArrowsOut size={17} /></button>
            </div>
            <div className="graph-toolbar"><div className="view-switch" role="tablist" aria-label="Graph view"><button role="tab" aria-selected={mode === 'architecture'} className={mode === 'architecture' ? 'active' : ''} onClick={() => switchGraphMode('architecture')}><Graph size={15} /> Code map</button><button role="tab" aria-selected={mode === 'decisions'} className={mode === 'decisions' ? 'active' : ''} onClick={() => switchGraphMode('decisions')}><ClockCounterClockwise size={15} /> Decision map</button></div><label className="graph-search"><MagnifyingGlass size={15} /><input aria-label="Search graph" placeholder="Find file or symbol" value={query} onChange={(event) => setQuery(event.target.value)} /><kbd>⌘ K</kbd></label><div className="filter-wrap"><button className="filter-button" aria-expanded={showGraphFilters} onClick={() => setShowGraphFilters((open) => !open)}><span className="filter-dots"><i /><i /><i /></span> Filters <ArrowDown size={13} /></button>{showGraphFilters && <div className="filter-menu" role="group" aria-label="Filter graph node types">{[['file','Files'],['module','Modules'],['symbol','Symbols'],['commit','Commits'],['pr','Pull requests'],['issue','Issues'],['person','People'],['decision','Decisions']].map(([kind,label]) => <label key={kind}><input type="checkbox" checked={!graphFilters || graphFilters.includes(kind)} onChange={(event) => setGraphFilters((current) => { const allKinds = ['file','module','symbol','commit','pr','issue','person','decision']; const selected = current ? [...current] : allKinds; const next = event.target.checked ? [...selected, kind].filter((item, index, list) => list.indexOf(item) === index) : selected.filter((item) => item !== kind); return next.length === allKinds.length ? null : next })} /> {label}</label>)}<button type="button" onClick={() => { setGraphFilters(null); setShowGraphFilters(false) }}>Reset filters</button></div>}</div></div>
            <div className="graph-panel">
              <div className="graph-backdrop-label">{mode === 'architecture' ? 'DEPENDENCY OVERVIEW' : 'EVIDENCE CHAIN'} <span>·</span> {liveGraph ? `${liveGraph.nodes.length} ENTITIES` : mode === 'architecture' ? '10 ENTITIES' : '7 RECORDS'}</div>
              {graphLoading && <div className="graph-loading" role="status"><span className="loading-ring" /> Loading repository graph</div>}
              <GraphCanvas mode={mode} query={query} graphData={liveGraph} onSelect={selectNode} selectedId={selectedId} filters={graphFilters} onZoom={setGraphZoom} />
              <div className="graph-legend"><span><i className="legend-module" /> Module</span><span><i className="legend-file" /> File</span><span><i className="legend-symbol" /> Symbol</span></div>
              <button className="graph-expand" aria-label="Fit graph" onClick={() => { const instance = document.querySelector('.graph-canvas'); instance?.dispatchEvent(new Event('fitgraph')) }}><ArrowsOut size={16} /></button>
              <div className="graph-zoom"><button aria-label="Zoom in" onClick={() => document.querySelector('.graph-canvas')?.dispatchEvent(new Event('zoom-in'))}>+</button><span>{graphZoom}%</span><button aria-label="Zoom out" onClick={() => document.querySelector('.graph-canvas')?.dispatchEvent(new Event('zoom-out'))}>−</button></div>
            </div>
            <div className="graph-footnote"><span><span className={`status-dot ${activeRepository ? '' : 'muted-dot'}`} /> {activeRepository ? activeIsSaved ? `Saved index · ${activeRepository.fullName} · private to your account` : `Temporary index · ${activeRepository.fullName} · deleted on exit or after inactivity` : 'Illustrative sample · repository data not yet indexed'}</span><span>DRAG TO PAN <i /> SCROLL TO ZOOM</span></div>
          </section>
          {showInspector && <aside className="inspector">
            <div className="inspector-tabs"><button className={activeSource === 'code' ? 'active' : ''} onClick={() => setActiveSource('code')}>Explore</button><button className={activeSource === 'assistant' ? 'active' : ''} onClick={() => setActiveSource('assistant')}>Assistant <Sparkle size={13} /></button><button className="inspector-close" aria-label="Close panel" onClick={() => setShowInspector(false)}><X size={16} /></button></div>
            {activeSource === 'assistant' ? <div className="assistant-panel">
              <div className="assistant-intro"><div className="assistant-mark"><Sparkle size={17} /></div><div><b>Repository assistant</b><small>{activeRepository ? 'BYOK · cited repository evidence' : 'Illustrative sample mode'}</small></div><button className="assistant-settings-button" aria-label="Model settings" title="Model settings" onClick={() => setShowAssistantSettings((open) => !open)}><GearSix size={16} /></button><span className="offline-pill">{!activeRepository ? 'PREVIEW' : assistantSettings.apiKey && assistantSettings.model ? 'CONNECTED' : 'SET UP KEY'}</span></div>
              {showAssistantSettings && <form className="assistant-settings" onSubmit={(event) => { event.preventDefault(); setShowAssistantSettings(false); setNotice('Model settings are saved for this browser tab only.') }}><div className="settings-heading"><b>Connect your model</b><button type="button" aria-label="Close model settings" onClick={() => setShowAssistantSettings(false)}><X size={14} /></button></div><label>OpenAI-compatible API URL<input value={assistantSettings.endpoint} onChange={(event) => { setAssistantSettings((settings) => ({ ...settings, endpoint: event.target.value })); setProviderTestMessage('') }} placeholder="https://api.provider.com/v1" autoComplete="url" /></label><label>Model ID from your provider<input value={assistantSettings.model} onChange={(event) => { setAssistantSettings((settings) => ({ ...settings, model: event.target.value })); setProviderTestMessage('') }} placeholder="model-id-from-provider" autoComplete="off" /></label><label>API key<input type="password" value={assistantSettings.apiKey} onChange={(event) => { setAssistantSettings((settings) => ({ ...settings, apiKey: event.target.value })); setProviderTestMessage('') }} placeholder="Paste your provider key" autoComplete="new-password" /></label><p>Use the provider’s base URL and exact model ID. Your key stays in this tab and goes directly from your browser to the provider. It is never sent to this app’s API. The provider must allow browser requests from this site (CORS).</p><div className="provider-test-actions"><button type="button" className="settings-test" onClick={() => void handleTestProvider()} disabled={providerTestBusy}>{providerTestBusy ? 'Testing…' : 'Test connection'}</button><button className="settings-save" type="submit">Save for this tab</button></div>{providerTestMessage && <p className="provider-test-message" role="status">{providerTestMessage}</p>}</form>}
              <div className="assistant-context"><span className="context-icon"><Code size={14} /></span><span>Context <b>{selected?.label ?? 'Repository'}</b></span><button aria-label="Clear context" onClick={() => setSelectedId('repo')}><X size={13} /></button></div>
              <div className="chat-messages">{chat.map((message, index) => <div className={`chat-message ${message.role}`} key={`${message.role}-${index}`}><div className="chat-avatar">{message.role === 'assistant' ? <Sparkle size={13} /> : 'Y'}</div><div className="chat-content"><p>{message.text}</p>{message.confidence && <span className={`confidence-badge confidence-${message.confidence}`}>{message.confidence} confidence</span>}{message.sources?.map((source) => <div className="source-chip" key={source}><BookOpenText size={13} /> {source}</div>)}{message.citations?.map((citation) => <a className="source-chip" href={citationHref(citation, activeRepository)} target="_blank" rel="noreferrer" key={citation.id}><BookOpenText size={13} /> {citation.kind.toUpperCase()} · {citation.label}</a>)}</div></div>)}{assistantBusy && <div className="assistant-thinking" role="status"><span className="loading-ring" /> Reviewing connected evidence…</div>}</div>
              <div className="suggested-questions"><span>TRY ASKING</span>{['Why does this file exist?', 'What changed this behavior?'].map((suggestion) => <button key={suggestion} onClick={() => setQuestion(suggestion)}>{suggestion}<ArrowRight size={13} /></button>)}</div>
              {activeRepository && <label className="provider-consent"><input type="checkbox" checked={shareWithProvider} onChange={(event) => setShareWithProvider(event.target.checked)} /><span>Send this question, selected code, and connected history to my model provider.</span></label>}
              <form className="chat-input-wrap" onSubmit={submitQuestion}><textarea value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit() } }} placeholder="Ask why this code exists…" rows={2} /><div className="chat-input-footer"><span><ShieldCheck size={13} /> Sources shown with every answer</span><button aria-label="Send question" disabled={!question.trim() || assistantBusy}>{assistantBusy ? <span className="button-spinner" /> : <PaperPlaneRight size={16} weight="fill" />}</button></div></form>
              <p className="assistant-disclaimer">{activeRepository ? 'Answers use only the indexed history window and are checked against returned source IDs. Verify important conclusions against linked records.' : 'Preview responses are illustrative and do not use repository data.'}</p>
            </div> : <div className="entity-inspector">
              <div className="entity-type"><span className={`entity-mark ${selected?.kind ?? 'file'}`}>{selected?.kind === 'symbol' ? <Code size={15} /> : selected?.kind === 'commit' ? <GitCommit size={15} /> : selected?.kind === 'pr' ? <GitPullRequest size={15} /> : selected?.kind === 'issue' ? <BookOpenText size={15} /> : <FileCode size={15} />}</span><span>{selected?.kind?.toUpperCase() ?? 'FILE'}</span><span className="verified-preview"><span /> PREVIEW</span></div>
              <h3>{selected?.label ?? 'Select a graph node'}</h3>
              <p className="entity-description">{selected?.description}</p>
              {(supersededBy.length > 0 || supersedes.length > 0) && <div className="superseded-notice"><b>EXPLICIT HISTORY LINK</b>{supersededBy.length > 0 && <><span>This record is explicitly superseded by:</span>{supersededBy.map((node) => <button key={`newer-${node.id}`} onClick={() => setSelectedId(node.id)}>{node.label}<ArrowRight size={12} /></button>)}</>}{supersedes.length > 0 && <><span>This record explicitly supersedes:</span>{supersedes.map((node) => <button key={`older-${node.id}`} onClick={() => setSelectedId(node.id)}>{node.label}<ArrowRight size={12} /></button>)}</>}</div>}
              {selected?.path && <button className="path-link" onClick={() => openGitHub(selected.path ?? undefined)}><Code size={14} /><span>{selected.path}</span><ArrowRight size={13} /></button>}
              {selected?.date && <div className="entity-meta"><span>DATE</span><b>{selected.date}</b></div>}
              {selected?.author && <div className="entity-meta"><span>{selected.kind === 'person' ? 'IDENTITY' : 'AUTHOR'}</span><b>{selected.author}</b></div>}
              <div className="code-card"><div className="code-card-head"><span><FileCode size={13} /> {previewName}</span><div><button disabled={!currentSource || currentSource.path !== previewPath} onClick={openSourceEditor}>EDIT DRAFT</button><button disabled={!previewPath} onClick={() => openGitHub(previewPath)}>OPEN FILE <ArrowRight size={12} /></button></div></div><div className="code-content">{sourceLines.length ? sourceLines.map(([line, code, kind]) => <div className="code-line" key={line}><span className="line-number">{line}</span><code className={kind}>{code || ' '}</code></div>) : <div className="source-state">{sourceError || (graphLoading ? 'Loading the selected file…' : previewPath ? 'Loading source from GitHub…' : 'Select a file node to preview its contents.')}</div>}</div><div className="code-card-foot"><span>{sourceLines.length ? `${sourceLines.length} LINES PREVIEWED${currentSource?.truncated ? ' · FILE TRUNCATED' : ''}` : 'SOURCE PREVIEW'}</span><button disabled={!previewPath} onClick={() => openGitHub(previewPath)}>VIEW ON GITHUB <ArrowRight size={12} /></button></div></div>
              <div className="evidence-preview"><div className="evidence-head"><span><span className="evidence-mark"><GitCommit size={13} /></span> {activeRepository ? 'CONNECTED GRAPH NODES' : 'HISTORY CONNECTIONS'}</span><span>{activeRepository ? connectedNodes.length : 2}</span></div>{activeRepository ? connectedNodes.length ? connectedNodes.slice(0, 8).map((node) => <button key={node.id} onClick={() => setSelectedId(node.id)}><span className="evidence-type">{node.kind.toUpperCase()}</span><span className="evidence-title">{node.label}</span><span className="evidence-sub">{node.date ?? node.description ?? 'Connected in the indexed graph'}</span><ArrowRight size={14} /></button>) : <p className="source-state">No connected records are present in this graph view.</p> : <><button onClick={() => switchGraphMode('decisions')}><span className="evidence-type">COMMIT</span><span className="evidence-title">Introduce shared defaults</span><span className="evidence-sub">Illustrative sample</span><ArrowRight size={14} /></button><button onClick={() => switchGraphMode('decisions')}><span className="evidence-type issue-type">ISSUE</span><span className="evidence-title">Inconsistent config behavior</span><span className="evidence-sub">Illustrative sample</span><ArrowRight size={14} /></button></>}</div>
              <button className="ask-why-button" onClick={() => setActiveSource('assistant')}><Sparkle size={15} /> Ask why this exists <ArrowRight size={14} /></button>
              <div className="provenance-note"><ShieldCheck size={14} /><span>{activeRepository ? 'Supersession links appear only when later pull request text explicitly names an older pull request as replaced. Missing links do not prove a decision is current.' : 'Sample content is illustrative. Linked sources are not asserted as factual history.'}</span></div>
            </div>}
          </aside>}{!showInspector && <button className="inspector-reopen" onClick={() => setShowInspector(true)}>Show details panel <ArrowRight size={14} /> </button>}
          {editorOpen && <section className="editor-overlay" aria-label="Unsaved file editor"><header className="editor-header"><div><span>LOCAL DRAFT · READ ONLY SOURCE</span><b>{selected?.path}</b></div><div><span>{editorText.split('\n').length.toLocaleString()} lines{currentSource?.truncated ? ' · fetched file is truncated at the safety limit' : ''}</span><button onClick={() => setEditorPath(null)} aria-label="Close editor"><X size={16} /></button></div></header><div className="editor-body"><div className="editor-column"><pre className="editor-gutter" aria-hidden="true" style={{ transform: `translateY(-${editorScrollTop}px)` }}>{editorText.split('\n').map((_, index) => index + 1).join('\n')}</pre><textarea className="source-editor" aria-label="Editable local source draft" value={editorText} onChange={(event) => setEditorDraft({ path: selected?.path ?? '', content: event.target.value })} onScroll={(event) => setEditorScrollTop(event.currentTarget.scrollTop)} onKeyDown={(event) => { if (event.key === 'Escape') setEditorPath(null); if (event.key === 'Tab') { event.preventDefault(); const input = event.currentTarget; input.setRangeText('    ', input.selectionStart, input.selectionEnd, 'end'); setEditorDraft({ path: selected?.path ?? '', content: input.value }) } }} spellCheck={false} autoCapitalize="off" autoComplete="off" autoCorrect="off" wrap="off" /></div><aside className="editor-impact"><span className="editor-eyebrow">CHANGE PREVIEW</span><h3>{editorDirty ? 'Unsaved draft changes' : 'No draft changes yet'}</h3><div className="draft-diff"><span><b>+{editorChanges.added}</b> approx. lines added</span><span><b>−{editorChanges.removed}</b> approx. lines removed</span></div><p>This is a local draft. Nothing is written to GitHub, Neo4j, or run by this app. The links below are known graph connections, not a runtime prediction.</p>{currentSource?.truncated && <p className="editor-warning">Only the first part of this large file was fetched. The draft cannot represent its unseen remainder.</p>}<button className="editor-ask" disabled={!editorDirty} onClick={() => { setQuestion('What could this unsaved code change affect? Explain only what the draft and connected repository evidence support.'); setActiveSource('assistant'); setEditorPath(null) }}><Sparkle size={14} /> Ask AI about this draft</button><p className="editor-consent-note">The assistant receives up to 8,000 characters of the draft only after you enable provider sharing and send your question.</p><h4>Known graph connections · {connectedNodes.length}</h4>{connectedNodes.length ? connectedNodes.slice(0, 12).map((node) => <div className="editor-impact-node" key={node.id}><span>{node.kind.toUpperCase()}</span><b>{node.label}</b></div>) : <div className="source-state">No related nodes are present in this graph view.</div>}<button className="editor-reset" disabled={!editorDirty} onClick={() => setEditorDraft(null)}>Reset to indexed source</button></aside></div></section>}
        </div>
      </section>}
      {notice && workspace && <div className="toast" role="status"><CheckCircle size={16} />{notice}<button aria-label="Dismiss notification" onClick={() => setNotice('')}><X size={14} /></button></div>}
      {showAuth && <div className="auth-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowAuth(false) }}>
        <section className="auth-dialog" role="dialog" aria-modal="true" aria-labelledby="auth-title">
          <button className="auth-close" aria-label="Close sign in" onClick={() => setShowAuth(false)}><X size={17} /></button>
          <span className="auth-eyebrow">YOUR REPOSITORY CONTEXT</span>
          <h2 id="auth-title">Sign in to Why</h2>
          <p>Choose a provider. You can save repository indexes to your account when you decide to.</p>
          <button className="auth-provider" disabled={!githubOAuthConfigured} onClick={loginWithGitHub}><GithubLogo size={17} weight="fill" /> Continue with GitHub {!githubOAuthConfigured && <small>Not configured</small>}</button>
          <button className="auth-provider" disabled={!googleOAuthConfigured} onClick={loginWithGoogle}><GoogleLogo size={17} /> Continue with Google {!googleOAuthConfigured && <small>Not configured</small>}</button>
          <div className="auth-divider"><span />or use email<span /></div>
          {authStep === 'credentials' ? <>
            <div className="auth-mode-tabs"><button className={authMode === 'signin' ? 'active' : ''} onClick={() => { setAuthMode('signin'); setAuthMessage('') }}>Sign in</button><button className={authMode === 'signup' ? 'active' : ''} disabled={!emailAuthConfigured} onClick={() => { setAuthMode('signup'); setAuthMessage('') }}>Create account</button></div>
            <form className="auth-email-form" onSubmit={(event) => void (authMode === 'signup' ? handleEmailSignup(event) : handleEmailLogin(event))}>
              <label htmlFor="auth-email">Email address</label>
              <input id="auth-email" type="email" autoComplete="email" required maxLength={254} value={authEmail} onChange={(event) => setAuthEmail(event.target.value)} placeholder="you@example.com" disabled={authBusy} />
              <label htmlFor="auth-password">Password</label>
              <input id="auth-password" type="password" autoComplete={authMode === 'signup' ? 'new-password' : 'current-password'} required minLength={authMode === 'signup' ? 12 : 1} maxLength={128} value={authPassword} onChange={(event) => setAuthPassword(event.target.value)} placeholder={authMode === 'signup' ? 'At least 12 characters' : 'Your password'} disabled={authBusy} />
              {authMode === 'signup' && <><label htmlFor="auth-password-confirm">Confirm password</label><input id="auth-password-confirm" type="password" autoComplete="new-password" required minLength={12} maxLength={128} value={authPasswordConfirm} onChange={(event) => setAuthPasswordConfirm(event.target.value)} placeholder="Enter it again" disabled={authBusy} />{!emailAuthConfigured && <small>Email signup is unavailable until SMTP is configured.</small>}</>}
              <button className="auth-submit" type="submit" disabled={authBusy || !authEmail.trim() || !authPassword || (authMode === 'signup' && (!emailAuthConfigured || !authPasswordConfirm))}>{authBusy ? 'Please wait…' : authMode === 'signup' ? 'Create email account' : 'Sign in with email'}</button>
              <small>{authMode === 'signup' ? 'We’ll email a one-time code to verify your address before creating the account. Your email and password hash are stored in the connected database.' : 'Email accounts can enable a sign-in code in Account settings.'}</small>
            </form>
          </> : <form className="auth-email-form" onSubmit={(event) => void handleVerifyEmailCode(event)}>
            <label htmlFor="auth-code">{authStep === 'signup-code' ? `Verification code sent to ${authEmail}` : `Sign-in code sent to ${authEmail}`}</label>
            <input id="auth-code" type="text" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required value={authCode} onChange={(event) => setAuthCode(event.target.value.replace(/\D/g, '').slice(0, 6))} placeholder="000000" disabled={authBusy} />
            <button className="auth-submit" type="submit" disabled={authBusy || authCode.length !== 6}>{authBusy ? 'Verifying…' : authStep === 'signup-code' ? 'Verify email and create account' : 'Verify and sign in'}</button>
            <button type="button" className="auth-back" onClick={() => { setAuthStep('credentials'); setAuthMessage('') }} disabled={authBusy}>Back to email and password</button>
          </form>}
          <div className="auth-privacy"><ShieldCheck size={14} /> Passwords are stored as Argon2id hashes, never as plain text. Codes expire after 10 minutes and are limited to five attempts.</div>
          {authMessage && <p className="auth-message" role="status">{authMessage}</p>}
        </section>
      </div>}
      {showAccountSettings && <div className="auth-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowAccountSettings(false) }}>
        <section className="auth-dialog account-dialog" role="dialog" aria-modal="true" aria-labelledby="account-title">
          <button className="auth-close" aria-label="Close account settings" onClick={() => setShowAccountSettings(false)}><X size={17} /></button>
          <span className="auth-eyebrow">SETTINGS / ACCOUNT</span>
          <h2 id="account-title">Account security</h2>
          {accountInfo ? <>
            <div className="account-identity"><span>{accountInfo.provider === 'email' ? 'EMAIL ACCOUNT' : `${accountInfo.provider?.toUpperCase() ?? 'ACCOUNT'} SIGN-IN`}</span><b>{accountInfo.email ?? githubLogin ?? 'Signed-in account'}</b></div>
            {accountInfo.provider === 'email' ? <>
              <form className="account-security-form" onSubmit={(event) => void handleToggleTwoFactor(event)}>
              <div className="account-2fa-state"><span><b>Email two-step verification</b><small>{accountInfo.two_factor_enabled ? 'A code is required each time you sign in.' : 'Password only at sign-in.'}</small></span><span className={`account-state-pill ${accountInfo.two_factor_enabled ? 'enabled' : ''}`}>{accountInfo.two_factor_enabled ? 'ON' : 'OFF'}</span></div>
              {!accountInfo.two_factor_available && <p>Configure Gmail SMTP before turning this on, so sign-in codes can be delivered.</p>}
              {!twoFactorEnrollment && <><label htmlFor="account-password">Confirm your password to change this setting</label>
              <input id="account-password" type="password" autoComplete="current-password" minLength={1} maxLength={128} value={accountPassword} onChange={(event) => setAccountPassword(event.target.value)} placeholder="Current password" disabled={accountBusy} />
              <button className="auth-submit" type="submit" disabled={accountBusy || !accountPassword || (!accountInfo.two_factor_enabled && !accountInfo.two_factor_available)}>{accountBusy ? 'Saving…' : accountInfo.two_factor_enabled ? 'Disable two-step verification' : 'Enable two-step verification'}</button></>}
              </form>
              {twoFactorEnrollment && <form className="account-security-form" onSubmit={(event) => void handleConfirmTwoFactorEnrollment(event)}>
                <label htmlFor="account-two-factor-code">Enter the six-digit setup code emailed to you</label>
                <input id="account-two-factor-code" type="text" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required value={twoFactorCode} onChange={(event) => setTwoFactorCode(event.target.value.replace(/\D/g, '').slice(0, 6))} placeholder="000000" disabled={accountBusy} />
                <button className="auth-submit" type="submit" disabled={accountBusy || twoFactorCode.length !== 6}>{accountBusy ? 'Verifying…' : 'Verify and enable'}</button>
              </form>}
            </> : <p>Two-step verification for this account is managed by {accountInfo.provider === 'google' ? 'Google' : 'GitHub'}.</p>}
          </> : <p>{accountMessage || 'Loading account settings…'}</p>}
          {accountMessage && accountInfo && <p className="auth-message" role="status">{accountMessage}</p>}
          <button className="auth-signout" onClick={() => void handleLogout()}>Sign out</button>
        </section>
      </div>}
    </main>
  )
}

export default App
