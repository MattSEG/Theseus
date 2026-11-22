import { useState } from 'react'
import { researchRequest } from './rpc'

type Task = { id: number; title: string; minutes: number | null; revision: number }
type Plan = {
  project: string
  as_of: string
  max_minutes: number | null
  receipt: string
  searched: string[]
  limit_per_group: number
  ready: Task[]
  awaiting_choice?: Task[]
  counts: {
    eligible: number
    without_estimate: number
    over_budget: number
    waiting: number
    needs_decision: number
    due: number
    workflows: number
    crawls: number
  }
  decisions: { id: number; title: string; due: string; state: string; event: string }[]
  workflows: { id: number; title: string; next_step: string; last_note: string | null; completed: number }[]
  crawls: {
    id: number
    question: string
    checkpoint: number | null
    last_note: string | null
    queued_pages: number
    unsettled_pages: number
  }[]
}
export function ProjectNext({ project }: { project: string }) {
  const [budget, setBudget] = useState(''),
    [plan, setPlan] = useState<Plan>(),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  async function check() {
    setBusy(true)
    setError('')
    try {
      setPlan(await researchRequest<Plan>('project_next', project, { max_minutes: budget ? Number(budget) : null }))
    } catch (e) {
      setPlan(undefined)
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <details className="theseus-next">
      <summary>Plan my next session</summary>
      <form
        className="research-actions"
        onSubmit={e => {
          e.preventDefault()
          void check()
        }}
      >
        <label>
          Minutes available in {project}
          <input
            aria-label="Minutes available in this project"
            type="number"
            step="1"
            min="1"
            max="10080"
            value={budget}
            onChange={e => {
              setBudget(e.target.value)
              setPlan(undefined)
            }}
          />
        </label>
        <button disabled={busy}>Check next steps</button>
      </form>
      {error && <p role="alert">{error}</p>}
      {plan && (
        <section aria-label="Project plan">
          <h4>{plan.max_minutes ? 'Ready within ' + plan.max_minutes + ' minutes' : 'Ready to start'}</h4>
          {plan.ready.length ? (
            <ul>
              {plan.ready.map(r => (
                <li key={r.id}>
                  {r.title} · {r.minutes === null ? 'No estimate' : r.minutes + ' min estimate'}
                </li>
              ))}
            </ul>
          ) : (
            <p>
              {plan.max_minutes
                ? 'No ready commitment has a recorded estimate that fits.'
                : 'No ready commitments recorded.'}
            </p>
          )}
          {plan.counts.eligible > plan.ready.length && (
            <small>
              Showing {plan.ready.length} of {plan.counts.eligible} eligible commitments.
            </small>
          )}
          <p>
            {plan.counts.without_estimate} ready without estimates · {plan.counts.over_budget} over budget ·{' '}
            {plan.counts.waiting} waiting
          </p>
          {plan.decisions.length > 0 && (
            <>
              <h4>Decisions due for review</h4>
              <ul>
                {plan.decisions.map(d => (
                  <li key={d.id}>
                    {d.title} · {d.state === 'Triggered' ? 'Event reported' : 'Due ' + d.due}
                  </li>
                ))}
              </ul>
            </>
          )}
          {!!plan.awaiting_choice?.length && (
            <>
              <h4>Commitments awaiting a decision</h4>
              <ul>
                {plan.awaiting_choice.map(r => (
                  <li key={r.id}>{r.title}</li>
                ))}
              </ul>
            </>
          )}
          {(plan.workflows.length > 0 || plan.crawls.length > 0) && (
            <>
              <h4>Where work paused</h4>
              <ul>
                {plan.workflows.map(w => (
                  <li key={'workflow' + w.id}>
                    {w.title} · {w.completed} steps recorded. Next step: {w.next_step}
                    {w.last_note && <p>{w.last_note}</p>}
                  </li>
                ))}
                {plan.crawls.map(c => (
                  <li key={'crawl' + c.id}>
                    {c.question} · {c.queued_pages} pages queued
                    {c.unsettled_pages > 0 ? ' · ' + c.unsettled_pages + ' unsettled' : ''}
                    {c.last_note && <p>{c.last_note}</p>}
                  </li>
                ))}
              </ul>
            </>
          )}
          <details>
            <summary>Records checked</summary>
            <p>
              {plan.project} · {plan.as_of}. Searched {plan.searched.join(', ')}. Up to {plan.limit_per_group} results
              per group.
            </p>
            <code>{plan.receipt}</code>
          </details>
        </section>
      )}
    </details>
  )
}
