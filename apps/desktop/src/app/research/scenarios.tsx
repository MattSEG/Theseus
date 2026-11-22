import { useEffect, useState } from 'react'
import { researchRequest } from './rpc'

const fields = {
  price: 'Selling price per unit',
  cost: 'Variable cost per unit',
  quantity: 'Units sold',
  fixed: 'Upfront fixed costs',
  lead_days: 'Days to deliver',
  supplier_days: 'Supplier payment day',
  customer_days: 'Customer terms after delivery (days)'
}
const names = {
  contribution: 'Contribution per unit',
  profit: 'Batch profit',
  break_even_units: 'Break-even units',
  price_floor: 'Minimum selling price at this volume',
  cash_before_collection: 'Cash needed before first collection',
  first_receipt_day: 'First collection day'
}
type Result = {
  id: number
  title: string
  inputs: Record<string, string> & { cost_reference?: { edition: number; quote: string } }
  result: Record<string, string | number | null>
}
export function Scenarios({ project }: { project: string }) {
  const [rows, setRows] = useState<Result[]>([]),
    [inputs, setInputs] = useState<Record<string, string>>({ currency: 'USD' }),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  const set = (key: string, value: string) => setInputs(old => ({ ...old, [key]: value }))
  useEffect(() => {
    let active = true
    researchRequest<{ scenarios: Result[] }>('scenario_list', project)
      .then(d => {
        if (active) setRows(d.scenarios)
      })
      .catch(e => {
        if (active) setError(String(e))
      })
    return () => {
      active = false
    }
  }, [project])
  async function save() {
    setBusy(true)
    setError('')
    try {
      const r = await researchRequest<{ scenarios: Result[] }>('scenario_save', project, {
        ...inputs,
        edition: inputs.edition ? Number(inputs.edition) : undefined
      })
      setRows(r.scenarios)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="research-editor">
      <h3>Batch scenario</h3>
      <p>
        All units sell in one batch. Fixed costs are paid up front. Supplier payment falls on the stated
        day from order. Same-day payments occur before collection.
      </p>
      <form
        onSubmit={e => {
          e.preventDefault()
          void save()
        }}
      >
        <fieldset disabled={busy}>
          <div className="research-limits">
            {Object.entries({
              title: 'Scenario name',
              currency: 'Currency',
              ...fields,
              edition: 'Cost source edition (optional)',
              quote: 'Exact cost passage (optional)'
            }).map(([k, label]) => (
              <label key={k}>
                {label}
                <input
                  required={!['edition', 'quote'].includes(k)}
                  value={inputs[k] || ''}
                  onChange={e => set(k, e.target.value)}
                />
              </label>
            ))}
          </div>
          <button>Save scenario</button>
        </fieldset>
      </form>
      {error && <p role="alert">{error}</p>}
      {rows.length > 0 && (
        <div className="research-table">
          <table>
            <thead>
              <tr>
                <th>Outcome</th>
                {rows.map(r => (
                  <th key={r.id}>
                    {r.title} · {r.inputs.currency}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Object.entries(names).map(([k, label]) => (
                <tr key={k}>
                  <th>{label}</th>
                  {rows.map(r => (
                    <td key={r.id}>{r.result[k] ?? 'No finite break-even'}</td>
                  ))}
                </tr>
              ))}
              <tr>
                <th>Cost basis</th>
                {rows.map(r => (
                  <td key={r.id}>
                    {r.inputs.cost_reference
                      ? 'Edition ' +
                        r.inputs.cost_reference.edition +
                        ' · ' +
                        r.inputs.cost_reference.quote
                      : 'Entered assumption'}
                  </td>
                ))}
              </tr>
              <tr>
                <th>Inputs</th>
                {rows.map(r => (
                  <td key={r.id}>
                    <details>
                      <summary>Inspect inputs</summary>
                      {Object.entries(fields).map(([k, v]) => (
                        <p key={k}>
                          {v} · {r.inputs[k]}
                        </p>
                      ))}
                      <button
                        onClick={() => {
                          const { cost_reference: ref, ...rest } = r.inputs
                          setInputs({
                            ...rest,
                            title: r.title + ' variation',
                            edition: ref ? String(ref.edition) : '',
                            quote: ref?.quote || ''
                          })
                        }}
                      >
                        Use these assumptions
                      </button>
                    </details>
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
