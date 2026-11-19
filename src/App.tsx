import { useEffect, useMemo, useRef, useState } from 'react'
import type { Core, ElementDefinition } from 'cytoscape'
import {
  ArrowDown, ArrowRight, ArrowsOut, BookOpenText,
  CheckCircle, CaretDown, ClockCounterClockwise, Code, Command, FileCode,
  FolderSimple, GithubLogo, GitCommit, GitPullRequest, Graph,
  MagnifyingGlass, PaperPlaneRight, Plus, ShieldCheck, Sparkle, X,
} from '@phosphor-icons/react'
import './App.css'

type GraphMode = 'architecture' | 'decisions'
type NodeKind = 'root' | 'module' | 'file' | 'symbol' | 'commit' | 'pr' | 'issue' | 'person' | 'decision'
type GraphNode = { id: string; label: string; kind: NodeKind; description: string; path?: string; date?: string; author?: string; content?: string; x: number; y: number }
type ChatMessage = { role: 'user' | 'assistant'; text: string; sources?: string[] }

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

const nodeColors: Record<NodeKind, string> = {
  root: '#d2f06c', module: '#566056', file: '#718176', symbol: '#a7b1a3',
  commit: '#8ea878', pr: '#84aaa0', issue: '#bd967c', person: '#9b91a9', decision: '#d2f06c',
}

function makeElements(mode: GraphMode, search: string): ElementDefinition[] {
  const sourceNodes = mode === 'architecture' ? architectureNodes : decisionNodes
  const normalizedSearch = search.trim().toLowerCase()
  const visible = sourceNodes.filter((node) => !normalizedSearch || `${node.label} ${node.description} ${node.path ?? ''}`.toLowerCase().includes(normalizedSearch))
  const visibleIds = new Set(visible.map((node) => node.id))
  return [
    ...visible.map((node) => ({ data: { id: node.id, label: node.label, kind: node.kind, color: nodeColors[node.kind] }, position: { x: node.x, y: node.y } })),
    ...graphEdges[mode].filter(([from, to]) => visibleIds.has(from) && visibleIds.has(to)).map(([source, target, label], index) => ({ data: { id: `${source}-${target}-${index}`, source, target, label } })),
  ]
}

function GraphCanvas({ mode, query, onSelect, selectedId }: { mode: GraphMode; query: string; onSelect: (id: string) => void; selectedId: string }) {
  const host = useRef<HTMLDivElement>(null)
  const graph = useRef<Core | null>(null)
  const [loadError, setLoadError] = useState(false)

  useEffect(() => {
    if (!host.current) return
    const container = host.current
    let instance: Core | null = null
    let cancelled = false
    let fit: (() => void) | undefined
    let zoomIn: (() => void) | undefined
    let zoomOut: (() => void) | undefined
    void import('cytoscape').then(({ default: cytoscape }) => {
      if (cancelled || !host.current) return
      instance = cytoscape({
        container,
        elements: makeElements(mode, query),
        layout: { name: 'preset', fit: true, padding: 64, animate: true, animationDuration: 450 },
        minZoom: 0.35, maxZoom: 2.1, wheelSensitivity: 0.2,
        style: [
          { selector: 'node', style: { 'background-color': 'data(color)', 'label': 'data(label)', 'color': '#e6e9df', 'font-size': '11px', 'font-family': 'Segoe UI, sans-serif', 'font-weight': 500, 'text-wrap': 'wrap', 'text-max-width': '120px', 'text-valign': 'bottom', 'text-margin-y': 11, 'text-outline-width': 2, 'text-outline-color': '#111410', 'width': 25, 'height': 25, 'border-width': 1, 'border-color': '#111410', 'overlay-opacity': 0, 'transition-property': 'width height border-width', 'transition-duration': 180 } },
          { selector: 'node[kind = "root"], node[kind = "decision"]', style: { width: 36, height: 36, 'border-width': 2, 'border-color': '#e7f5bf', 'font-size': '12px' } },
          { selector: 'node[kind = "module"]', style: { shape: 'round-rectangle', width: 30, height: 30 } },
          { selector: 'node:selected', style: { 'border-width': 3, 'border-color': '#d2f06c', 'width': 34, 'height': 34, 'z-index': 999 } },
          { selector: 'edge', style: { 'curve-style': 'bezier', 'line-color': '#40473d', 'target-arrow-color': '#737d6b', 'target-arrow-shape': 'triangle', 'arrow-scale': 0.72, 'width': 1.2, 'label': 'data(label)', 'font-size': '8px', 'color': '#9ca496', 'text-background-color': '#111410', 'text-background-opacity': 0.92, 'text-background-padding': '3px', 'text-rotation': 'autorotate', 'opacity': 0.85 } },
          { selector: 'edge:selected', style: { 'line-color': '#d2f06c', 'target-arrow-color': '#d2f06c', width: 2 } },
        ],
      })
      graph.current = instance
      instance.on('tap', 'node', (event) => onSelect(event.target.id()))
      fit = () => instance?.fit(undefined, 64)
      zoomIn = () => instance?.zoom({ level: Math.min(instance.zoom() * 1.2, 2.1), renderedPosition: { x: instance.width() / 2, y: instance.height() / 2 } })
      zoomOut = () => instance?.zoom({ level: Math.max(instance.zoom() / 1.2, 0.35), renderedPosition: { x: instance.width() / 2, y: instance.height() / 2 } })
      container.addEventListener('fitgraph', fit)
      container.addEventListener('zoom-in', zoomIn)
      container.addEventListener('zoom-out', zoomOut)
    }).catch(() => { if (!cancelled) setLoadError(true) })
    return () => {
      cancelled = true
      if (fit) container.removeEventListener('fitgraph', fit)
      if (zoomIn) container.removeEventListener('zoom-in', zoomIn)
      if (zoomOut) container.removeEventListener('zoom-out', zoomOut)
      instance?.destroy()
      if (graph.current === instance) graph.current = null
    }
  }, [mode, query, onSelect])

  useEffect(() => {
    if (!graph.current) return
    graph.current.nodes().unselect()
    if (selectedId && graph.current.getElementById(selectedId).length) graph.current.getElementById(selectedId).select()
  }, [selectedId])

  return <div className="graph-canvas" ref={host} aria-label={`${mode === 'architecture' ? 'Code structure' : 'Decision history'} graph`}>{loadError && <div className="graph-error" role="status">The graph view could not load. Refresh the workspace to try again.</div>}</div>
}

function App() {
  const [mode, setMode] = useState<GraphMode>('architecture')
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState('routefn')
  const [repoUrl, setRepoUrl] = useState('')
  const [workspace, setWorkspace] = useState(false)
  const [chat, setChat] = useState<ChatMessage[]>([{ role: 'assistant', text: 'Ask about this codebase. I’ll trace the answer back to repository evidence and call out when the history can’t support a conclusion.' }])
  const [question, setQuestion] = useState('')
  const [activeSource, setActiveSource] = useState('code')
  const [notice, setNotice] = useState('')
  const nodes = mode === 'architecture' ? architectureNodes : decisionNodes
  const selected = nodes.find((node) => node.id === selectedId) ?? nodes[0]
  const selectNode = useMemo(() => (id: string) => setSelectedId(id), [])

  const handleSubmitRepo = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const normalized = repoUrl.trim()
    if (!normalized) { setNotice('Paste a public GitHub repository URL to continue.'); return }
    setNotice('Live repository indexing is coming in the next build phase. You can explore the clearly labeled sample workspace now.')
  }

  const handleDemo = () => {
    setWorkspace(true)
    setNotice('You are viewing an illustrative sample workspace. No repository data has been fetched yet.')
  }

  const submitQuestion = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const text = question.trim()
    if (!text) return
    setChat((current) => [...current, { role: 'user', text }, { role: 'assistant', text: 'This preview uses illustrative repository content. Once a repository is indexed, I’ll answer from linked code, commits, and discussions, with the source and date attached to each finding.', sources: ['Source citations will appear after indexing'] }])
    setQuestion('')
  }

  const openGitHub = (path?: string) => {
    if (!path) return
    const url = `https://github.com/vercel/next.js/blob/canary/${path}`
    window.open(url, '_blank', 'noopener,noreferrer')
  }

  return (
    <main className="app-shell">
      {!workspace ? <section className="landing">
        <header className="landing-nav">
          <a className="brand" href="#top" aria-label="Why home"><span className="brand-mark"><Graph size={17} weight="bold" /></span><span>why<span className="brand-period">.</span></span></a>
          <div className="nav-right"><span className="nav-status"><span className="status-dot" /> Local-first analysis</span><button className="nav-link" onClick={handleDemo}>Explore sample <ArrowRight size={15} /></button></div>
        </header>
        <div className="landing-grid" id="top">
          <section className="hero-copy">
            <div className="eyebrow"><span className="eyebrow-line" /> REPOSITORY INTELLIGENCE</div>
            <h1>Code tells you <em>what.</em><br />History tells you <em>why.</em></h1>
            <p className="hero-lede">Trace the reasoning behind a codebase. Connect files to the commits, conversations, and decisions that shaped them.</p>
            <form className="repo-form" onSubmit={handleSubmitRepo}>
              <GithubLogo size={19} weight="fill" />
              <input aria-label="Public GitHub repository URL" value={repoUrl} onChange={(event) => setRepoUrl(event.target.value)} placeholder="github.com/owner/repository" />
              <button type="submit">Analyze repo <ArrowRight size={16} /></button>
            </form>
            <div className="form-caption"><ShieldCheck size={15} /> Public repositories · Read-only access · No code execution</div>
            <button className="folder-entry" onClick={() => setNotice('Folder import will be available in the native app. The web version analyzes public GitHub repositories.')}> <FolderSimple size={17} /> Working with a local clone? <span>Import folder in the desktop app <ArrowRight size={14} /></span></button>
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
          <a className="brand" href="#top" onClick={(event) => { event.preventDefault(); setWorkspace(false) }}><span className="brand-mark"><Graph size={16} weight="bold" /></span><span>why<span className="brand-period">.</span></span></a>
          <div className="repo-crumb"><span className="crumb-slash">/</span><GithubLogo size={15} weight="fill" /><b>vercel / next.js</b><span className="sample-tag">SAMPLE</span><CaretDown size={13} /></div>
          <div className="topbar-right"><span className="indexed-status"><span className="status-dot muted-dot" /> Preview data only</span><button className="topbar-icon" aria-label="Open command menu"><Command size={17} /></button><button className="topbar-new" onClick={() => setWorkspace(false)}><Plus size={16} /> New repo</button></div>
        </header>
        <div className="workspace-body">
          <aside className="left-rail">
            <button className="rail-item selected" aria-label="Graph view"><Graph size={19} weight="fill" /></button>
            <button className="rail-item" aria-label="Code browser" onClick={() => setActiveSource('code')}><FileCode size={19} /></button>
            <button className="rail-item" aria-label="Decision timeline" onClick={() => setMode('decisions')}><ClockCounterClockwise size={19} /></button>
            <span className="rail-spacer" />
            <button className="rail-item" aria-label="Security and privacy"><ShieldCheck size={19} /></button>
            <div className="rail-avatar">W</div>
          </aside>
          <section className="graph-area">
            <div className="workspace-heading">
              <div><div className="workspace-kicker"><span>REPOSITORY MAP</span><span className="kicker-divider">/</span><span>{mode === 'architecture' ? 'CODE STRUCTURE' : 'DECISION HISTORY'}</span></div><h2>{mode === 'architecture' ? 'How the pieces connect' : 'How the decisions evolved'}</h2></div>
              <button className="graph-action" aria-label="Fit graph to screen" onClick={() => document.querySelector('.graph-canvas')?.dispatchEvent(new Event('fitgraph'))}><ArrowsOut size={17} /></button>
            </div>
            <div className="graph-toolbar"><div className="view-switch" role="tablist" aria-label="Graph view"><button role="tab" aria-selected={mode === 'architecture'} className={mode === 'architecture' ? 'active' : ''} onClick={() => setMode('architecture')}><Graph size={15} /> Code map</button><button role="tab" aria-selected={mode === 'decisions'} className={mode === 'decisions' ? 'active' : ''} onClick={() => setMode('decisions')}><ClockCounterClockwise size={15} /> Decision map</button></div><label className="graph-search"><MagnifyingGlass size={15} /><input aria-label="Search graph" placeholder="Find file or symbol" value={query} onChange={(event) => setQuery(event.target.value)} /><kbd>⌘ K</kbd></label><button className="filter-button"><span className="filter-dots"><i /><i /><i /></span> Filters <ArrowDown size={13} /></button></div>
            <div className="graph-panel">
              <div className="graph-backdrop-label">{mode === 'architecture' ? 'DEPENDENCY OVERVIEW' : 'EVIDENCE CHAIN'} <span>·</span> {mode === 'architecture' ? '10 ENTITIES' : '7 RECORDS'}</div>
              <GraphCanvas mode={mode} query={query} onSelect={selectNode} selectedId={selectedId} />
              <div className="graph-legend"><span><i className="legend-module" /> Module</span><span><i className="legend-file" /> File</span><span><i className="legend-symbol" /> Symbol</span></div>
              <button className="graph-expand" aria-label="Fit graph" onClick={() => { const instance = document.querySelector('.graph-canvas'); instance?.dispatchEvent(new Event('fitgraph')) }}><ArrowsOut size={16} /></button>
              <div className="graph-zoom"><button onClick={() => document.querySelector('.graph-canvas')?.dispatchEvent(new Event('zoom-in'))}>+</button><span>100%</span><button onClick={() => document.querySelector('.graph-canvas')?.dispatchEvent(new Event('zoom-out'))}>−</button></div>
            </div>
            <div className="graph-footnote"><span><span className="status-dot muted-dot" /> Illustrative sample · repository data not yet indexed</span><span>DRAG TO PAN <i /> SCROLL TO ZOOM</span></div>
          </section>
          <aside className="inspector">
            <div className="inspector-tabs"><button className={activeSource === 'code' ? 'active' : ''} onClick={() => setActiveSource('code')}>Explore</button><button className={activeSource === 'assistant' ? 'active' : ''} onClick={() => setActiveSource('assistant')}>Assistant <Sparkle size={13} /></button><button className="inspector-close" aria-label="Close panel"><X size={16} /></button></div>
            {activeSource === 'assistant' ? <div className="assistant-panel">
              <div className="assistant-intro"><div className="assistant-mark"><Sparkle size={17} /></div><div><b>Repository assistant</b><small>Grounded in indexed evidence</small></div><span className="offline-pill">PREVIEW</span></div>
              <div className="assistant-context"><span className="context-icon"><Code size={14} /></span><span>Context <b>{selected?.label ?? 'Repository'}</b></span><button aria-label="Clear context" onClick={() => setSelectedId('repo')}><X size={13} /></button></div>
              <div className="chat-messages">{chat.map((message, index) => <div className={`chat-message ${message.role}`} key={`${message.role}-${index}`}><div className="chat-avatar">{message.role === 'assistant' ? <Sparkle size={13} /> : 'Y'}</div><div className="chat-content"><p>{message.text}</p>{message.sources?.map((source) => <div className="source-chip" key={source}><BookOpenText size={13} /> {source}</div>)}</div></div>)}</div>
              <div className="suggested-questions"><span>TRY ASKING</span>{['Why does this file exist?', 'What changed this behavior?'].map((suggestion) => <button key={suggestion} onClick={() => setQuestion(suggestion)}>{suggestion}<ArrowRight size={13} /></button>)}</div>
              <form className="chat-input-wrap" onSubmit={submitQuestion}><textarea value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit() } }} placeholder="Ask why this code exists…" rows={2} /><div className="chat-input-footer"><span><ShieldCheck size={13} /> Sources shown with every answer</span><button aria-label="Send question" disabled={!question.trim()}><PaperPlaneRight size={16} weight="fill" /></button></div></form>
              <p className="assistant-disclaimer">Preview responses are illustrative. Connect a model after indexing to ask about a real repository.</p>
            </div> : <div className="entity-inspector">
              <div className="entity-type"><span className={`entity-mark ${selected?.kind ?? 'file'}`}>{selected?.kind === 'symbol' ? <Code size={15} /> : selected?.kind === 'commit' ? <GitCommit size={15} /> : selected?.kind === 'pr' ? <GitPullRequest size={15} /> : selected?.kind === 'issue' ? <BookOpenText size={15} /> : <FileCode size={15} />}</span><span>{selected?.kind?.toUpperCase() ?? 'FILE'}</span><span className="verified-preview"><span /> PREVIEW</span></div>
              <h3>{selected?.label ?? 'Select a graph node'}</h3>
              <p className="entity-description">{selected?.description}</p>
              {selected?.path && <button className="path-link" onClick={() => openGitHub(selected.path)}><Code size={14} /><span>{selected.path}</span><ArrowRight size={13} /></button>}
              {selected?.date && <div className="entity-meta"><span>DATE</span><b>{selected.date}</b></div>}
              {selected?.author && <div className="entity-meta"><span>{selected.kind === 'person' ? 'IDENTITY' : 'AUTHOR'}</span><b>{selected.author}</b></div>}
              <div className="code-card"><div className="code-card-head"><span><FileCode size={13} /> config-shared.ts</span><button onClick={() => openGitHub('packages/next/src/server/config-shared.ts')}>OPEN FILE <ArrowRight size={12} /></button></div><div className="code-content">{codePreview.map(([line, code, kind]) => <div className="code-line" key={line}><span className="line-number">{line}</span><code className={kind}>{code || ' '}</code></div>)}</div><div className="code-card-foot"><span>9 LINES SHOWN</span><button onClick={() => openGitHub('packages/next/src/server/config-shared.ts')}>VIEW ON GITHUB <ArrowRight size={12} /></button></div></div>
              <div className="evidence-preview"><div className="evidence-head"><span><span className="evidence-mark"><GitCommit size={13} /></span> HISTORY CONNECTIONS</span><span>2</span></div><button onClick={() => setMode('decisions')}><span className="evidence-type">COMMIT</span><span className="evidence-title">Introduce shared defaults</span><span className="evidence-sub">Mar 18, 2023 · Tim Neutkens</span><ArrowRight size={14} /></button><button onClick={() => setMode('decisions')}><span className="evidence-type issue-type">ISSUE</span><span className="evidence-title">Inconsistent config behavior</span><span className="evidence-sub">Illustrative link · preview only</span><ArrowRight size={14} /></button></div>
              <button className="ask-why-button" onClick={() => setActiveSource('assistant')}><Sparkle size={15} /> Ask why this exists <ArrowRight size={14} /></button>
              <div className="provenance-note"><ShieldCheck size={14} /><span>Sample content is illustrative. Linked sources are not asserted as factual history.</span></div>
            </div>}
          </aside>
        </div>
      </section>}
      {notice && workspace && <div className="toast" role="status"><CheckCircle size={16} />{notice}<button aria-label="Dismiss notification" onClick={() => setNotice('')}><X size={14} /></button></div>}
    </main>
  )
}

export default App
