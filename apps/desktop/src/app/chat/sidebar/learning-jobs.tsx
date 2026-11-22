import { useEffect, useState } from 'react'

import { ResearchWorkbench } from '@/app/research'
import { isDesktopFsRemoteMode } from '@/lib/desktop-fs'
import { localPreviewTarget } from '@/lib/local-preview'
import { openPreview } from '@/store/preview'

interface LearningJob {
  run: number
  job?: string | null
  problem: string
  phase: string
  recommendation?: string | null
  decision: string | null
  superseded_by?: number | null
}

const phases: Record<string, string> = {
  baseline: 'Baseline recorded',
  proposal: 'Proposed',
  staging: 'Patch staged',
  evaluation: 'Awaiting review',
  review: 'Reviewed'
}

function isJob(value: unknown): value is LearningJob {
  if (!value || typeof value !== 'object') { return false }

  return 'run' in value && typeof value.run === 'number' && Number.isSafeInteger(value.run) && value.run > 0
    && 'problem' in value && typeof value.problem === 'string'
    && (!('job' in value) || value.job === null || typeof value.job === 'string')
    && 'phase' in value && typeof value.phase === 'string' && Object.hasOwn(phases, value.phase)
    && (!('superseded_by' in value) || value.superseded_by === null || Number.isSafeInteger(value.superseded_by))
    && (!('recommendation' in value) || value.recommendation === null || ['keep', 'revert'].includes(String(value.recommendation)))
    && 'decision' in value && (value.decision === null || ['keep', 'revert'].includes(String(value.decision)))
}

function jobStatus(job: LearningJob) {
  if (job.superseded_by) { return `Superseded by run ${job.superseded_by}` }
  if (job.decision) { return `Human decision · ${job.decision}` }
  if (job.phase === 'evaluation' && job.recommendation) {
    return `Awaiting review · Evaluator recommends ${job.recommendation}`
  }
  return phases[job.phase]
}

export function LearningJobs({ directory }: { directory?: string }) {
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState(false)
  const [jobs, setJobs] = useState<LearningJob[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const local = Boolean(directory) && !isDesktopFsRemoteMode()

  useEffect(() => {
    if (!expanded || !local) { return }

    const desktop = window.hermesDesktop
    const path = directory + '/index.json'
    let active = true
    let version = 0
    let watchId = ''

    const refresh = async () => {
      const reading = ++version
      setLoading(true)
      try {
        const result = await desktop.readFileText(path)
        if (!('text' in result) || result.truncated) { throw new Error() }
        const rows: unknown = JSON.parse(result.text)
        if (!Array.isArray(rows) || !rows.every(isJob)) { throw new Error() }
        if (active && reading === version) {
          setJobs(rows)
          setError('')
        }
      } catch {
        if (active && reading === version) {
          setJobs([])
          setError('Could not read the learning journal.')
        }
      } finally {
        if (active && reading === version) { setLoading(false) }
      }
    }

    const unsubscribe = desktop.onPreviewFileChanged(event => {
      if (event.path === path) { void refresh() }
    })
    void desktop.watchPreviewFile(path).then(watch => {
      if (!('id' in watch)) { return }
      if (!active) {
        void desktop.stopPreviewFileWatch(watch.id)
        return
      }
      watchId = watch.id
      // Close the gap between the initial read and native watch registration.
      void refresh()
    }).catch(() => { /* The journal can still be reopened manually. */ })
    void refresh()

    return () => {
      active = false
      unsubscribe()
      if (watchId) { void desktop.stopPreviewFileWatch(watchId) }
    }
  }, [expanded, directory, local])

  if (!local || !directory) { return null }
  const profileDirectory = directory.slice(0, directory.lastIndexOf('/'))

  const needle = query.trim().toLowerCase()
  const visibleJobs = jobs.map(job => ({
    job, status: jobStatus(job)
  })).filter(({ job, status }) =>
    `run ${job.run} ${job.problem} ${job.job || ''} ${status}`.toLowerCase().includes(needle)
  )

  return (
    <section className="mx-3 my-1 text-sm">
      <ResearchWorkbench />
      <button type="button" className="w-full py-1 text-left font-medium" onClick={() => {
        const target = localPreviewTarget(profileDirectory + '/project-recall/investigations.html')
        if (target) { openPreview(target) }
      }}>Investigations</button>
      <button aria-expanded={expanded} className="w-full py-1 text-left font-medium" type="button" onClick={() => {
        setExpanded(!expanded)
      }}>Learning jobs</button>
      {expanded && <div aria-busy={loading}>
        <input aria-label="Search learning jobs" className="w-full rounded border bg-transparent px-2 py-1"
          placeholder="Search learning jobs" type="search" value={query} onChange={e => setQuery(e.target.value)} />
        <div className="max-h-52 overflow-auto">
          {error && <p role="alert">{error}</p>}
          {!error && !loading && !jobs.length && <p>No learning runs yet.</p>}
          {!error && !loading && jobs.length > 0 && !visibleJobs.length && <p>No matching learning jobs.</p>}
          {visibleJobs.map(({ job, status }) => (
            <button aria-label={`${job.job ? job.job + ' · ' : ''}${job.problem} · Run ${job.run} · ${status}`}
              className="block w-full rounded px-2 py-1 text-left hover:bg-muted" key={job.run} onClick={() => {
                const target = localPreviewTarget(directory + '/run-' + job.run + '.html')
                if (target) { openPreview(target) }
            }} type="button">
              <span className="block truncate">{job.job || job.problem}</span>
              {job.job && <span className="block truncate text-xs text-muted-foreground">{job.problem}</span>}
              <span className="text-xs text-muted-foreground">Run {job.run} · {status}</span>
            </button>
          ))}
        </div>
      </div>}
    </section>
  )
}
