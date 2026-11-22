import { useEffect, useRef, useState } from 'react'
import { researchRequest } from './rpc'
import { openPreview } from '@/store/preview'
import { localPreviewTarget } from '@/lib/local-preview'

import type { Example } from './predictions'

type Arm = 'current' | 'without_history' | 'with_history'
const names: Record<Arm, string> = {
  current: 'Current instructions', without_history: 'Proposal without history', with_history: 'Proposal with history'
}
type Experiment = {
  status: 'ready' | 'starting' | 'running' | 'follow_up' | 'interrupted' | 'failed' | 'complete'
  key?: string
  run?: number
  can_apply?: boolean
  active?: {id: number; run?: number; action?: string; rule: string}
  reason?: string
  rounds_left?: number
  parent?: string
  step?: string
  done?: number
  error?: string
  result?: {
    winner: Arm; scores: Record<Arm, number>; checks: number; tied_candidates: boolean
    decision: string; prior: string[]; rules: Record<Arm, string>; test_order: string[]; report: string
    follow_reason?: string; criteria?: Record<Arm, { values: number; citations: number; format: number }>
    used_prior?: number[]; rationales?: Partial<Record<Arm,string>>; by_case?: Record<Arm,number[]>
  }
}
export function LearningPilot({ project, close, example, clear }: { project: string; close: () => void; example?:Example; clear:()=>void }) {
  const [state, setState] = useState<Experiment>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const serial = useRef(0)
  const [followUp, setFollowUp] = useState(false)
  const running = state?.status === 'starting' || state?.status === 'running' || state?.status === 'follow_up'
  useEffect(() => {
    let active = true
    async function refresh() {
      const request = ++serial.current
      try {
        const result = await researchRequest<Experiment>('pilot_status', project)
        if (active && request === serial.current) { setState(result); setError('') }
      } catch (e) { if (active && request === serial.current) setError(String(e)) }
    }
    void refresh()
    const timer = running ? window.setInterval(() => void refresh(), 2000) : undefined
    return () => { active = false; window.clearInterval(timer) }
  }, [project, running])
  async function act(action: string) {
    ++serial.current
    setBusy(true)
    try {
      const next = await researchRequest<Experiment>(action, project, {expected: state?.key ?? null, follow_up: followUp, head: state?.active?.id ?? 0, review:example?.review, digest:example?.digest})
      setState(next); setError(''); if(action==='pilot_start' && next.key!==state?.key)clear()
    } catch (e) { setError(String(e)) }
    finally { setBusy(false) }
  }
  return <section aria-label="Harness learning experiment">
    <h3>Test a harness improvement</h3>
    <p>Test project-comparison instructions with and without learning history. One experiment uses up to 12 Sol turns on generated cases. You review any proposed change.</p>
    {example && <p>Selected for both proposal writers: {example.title}, revision {example.revision}. <button onClick={clear}>Clear selection</button></p>}
    {state?.run && <p>{state.parent ? 'Follow-up learning run' : 'Learning run'} {state.run}</p>}
    {state?.reason && <p>{state.reason}</p>}
    {state?.active?.action === 'apply' && <details>
      <summary>Active instruction from run {state.active.run}</summary>
      <p>{state.active.rule}</p>
      <button disabled={busy || running} onClick={() => void act('pilot_restore')}>Restore previous instructions</button>
    </details>}
    {running && <p role="status">{state?.step} · {state?.done ?? 0}/12 turns saved</p>}
    {state?.result && <div>
      <h4>{state.result.tied_candidates ? 'Tied proposals · current instructions retained' : state.result.decision}</h4>
      <ul>{(Object.keys(names) as Arm[]).map(arm => <li key={arm}>{names[arm]} · {state.result!.scores[arm]}/{state.result!.checks} checks</li>)}</ul>
      {state.result.winner !== 'current' && !state.result.tied_candidates && <p>{state.result.rules[state.result.winner]}</p>}
      {state.result.tied_candidates && <p>Both proposals improved the score by the same amount. Neither has been selected.</p>}
      {state.can_apply &&
        <button disabled={busy || running} onClick={() => void act('pilot_apply')}>Approve and use this comparison rule</button>}
      {state.result.follow_reason && <p>{state.result.follow_reason}</p>}
      <details><summary>Experiment details</summary>
        <p>Each case scores three values, three citation sets, and the answer format.</p>
        {state.result.criteria && <table><thead><tr><th>Instructions</th><th>Correct values</th><th>Exact citation sets</th><th>Format</th></tr></thead>
          <tbody>{(Object.keys(names) as Arm[]).map(arm => { const row=state.result!.criteria![arm]
            return <tr key={arm}><th>{names[arm]}</th><td>{row.values}/9</td><td>{row.citations}/9</td><td>{row.format}/3</td></tr>
          })}</tbody></table>}
        <p>Prior runs supplied: {state.result.prior.join(', ') || 'none'}</p>
        {state.result.used_prior && <p>Prior runs cited by the proposal: {state.result.used_prior.join(', ') || 'none'}</p>}
        {state.result.by_case && <table><thead><tr><th>Held-out case</th><th>Current</th><th>Without history</th><th>With history</th></tr></thead><tbody>
          {state.result.test_order.map((name,i)=><tr key={name}><th>{name}</th>
            {(['current','without_history','with_history'] as const).map(arm=><td key={arm}>{state.result!.by_case![arm][i]}/7</td>)}</tr>)}
        </tbody></table>}
        {(['without_history','with_history'] as const).map(arm=><p key={arm}><strong>{names[arm]}.</strong> {state.result!.rules[arm]} {state.result!.rationales?.[arm]}</p>)}
        <button onClick={() => {
          const target=localPreviewTarget(state.result!.report)
          if (target) { close(); openPreview(target) }
        }}>Open learning report</button>
      </details>
    </div>}
    {(error || state?.error) && <p role="alert">{error || state?.error}</p>}
    {state?.status === 'interrupted' && <p>{state.step}</p>}
    {state && !running && state.status !== 'interrupted' && <label>
      <input type="checkbox" checked={followUp} onChange={e=>setFollowUp(e.target.checked)} />
      Allow one follow-up if the first result is inconclusive (24 turns total)
    </label>}
    {state && !running && <div className="research-actions">
      {['interrupted','failed'].includes(state.status) && <button disabled={busy} onClick={() => void act('pilot_resume')}>Resume experiment</button>}
      {state.status !== 'interrupted' && <button disabled={busy} onClick={() => void act('pilot_start')}>{state.key ? 'Run new experiment' : 'Run experiment'}</button>}
    </div>}
  </section>
}
