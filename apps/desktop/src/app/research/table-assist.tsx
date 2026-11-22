import { useEffect, useState } from 'react'
import { researchRequest } from './rpc'

export type TableHint = {
  source: number
  edition: number
  sha256: string
  measure: string
  unit: string
  group: string
  records: number
}
type Preview = TableHint & {
  excluded_count: number
  excluded: { record: number; reason: string }[]
  groups: { group: string; n: number; minimum: string; maximum: string; min_record: number; max_record: number }[]
}
export function TableSetting({ project }: { project: string }) {
  const [enabled, setEnabled] = useState<boolean>(),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('')
  useEffect(() => {
    let active = true
    researchRequest<{ enabled: boolean }>('analysis_suggestions', project)
      .then(r => {
        if (active) setEnabled(r.enabled)
      })
      .catch(e => {
        if (active) setError(String(e))
      })
    return () => {
      active = false
    }
  }, [project])
  async function toggle(value: boolean) {
    setBusy(true)
    try {
      const r = await researchRequest<{ enabled: boolean }>('analysis_suggestions', project, {
        enabled: value,
        expected: enabled
      })
      setEnabled(r.enabled)
      setError('')
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <label>
        <input
          type="checkbox"
          disabled={busy || enabled === undefined}
          checked={enabled ?? false}
          onChange={e => void toggle(e.target.checked)}
        />
        Suggest analyses for imported tables
      </label>
      {error && <p role="alert">{error}</p>}
    </>
  )
}
export function TableAssist({ project, hint }: { project: string; hint: TableHint }) {
  const [preview, setPreview] = useState<Preview>(),
    [saved, setSaved] = useState(''),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('')
  async function run(save: boolean) {
    setBusy(true)
    setError('')
    try {
      const args = { source: hint.source, edition: hint.edition, sha256: hint.sha256 }
      if (save) {
        const r = await researchRequest<{ run: number; title: string }>('analysis_keep_preview', project, args)
        setSaved('Saved analysis ' + r.run + ' · ' + r.title)
      } else setPreview(await researchRequest<Preview>('analysis_preview', project, args))
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <section aria-label="Suggested table analysis">
      <h4>
        {hint.measure} by {hint.group}
      </h4>
      <p>
        {hint.records} records · edition {hint.edition}
      </p>
      {!preview && (
        <button disabled={busy} onClick={() => void run(false)}>
          Preview observed ranges
        </button>
      )}
      {preview && (
        <>
          <table>
            <thead>
              <tr>
                <th>{hint.group}</th>
                <th>Count</th>
                <th>Minimum ({hint.unit})</th>
                <th>Maximum ({hint.unit})</th>
              </tr>
            </thead>
            <tbody>
              {preview.groups.map(g => (
                <tr key={g.group}>
                  <th>{g.group}</th>
                  <td>{g.n}</td>
                  <td>
                    {g.minimum} <small>· record {g.min_record}</small>
                  </td>
                  <td>
                    {g.maximum} <small>· record {g.max_record}</small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p>
            {preview.records} source records · {preview.excluded_count} excluded
          </p>
          <details>
            <summary>Source and exclusions</summary>
            <p>
              Edition {preview.edition} · SHA-256 <code>{preview.sha256}</code>
            </p>
            {preview.excluded.map(e => (
              <p key={e.record}>
                Record {e.record} · {e.reason}
              </p>
            ))}
          </details>
          {saved ? (
            <p role="status">{saved}</p>
          ) : (
            <button disabled={busy} onClick={() => void run(true)}>
              Save this analysis
            </button>
          )}
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  )
}
