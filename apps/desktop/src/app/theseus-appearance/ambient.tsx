import { useEffect, useRef, useSyncExternalStore } from 'react'
import { useStore } from '@nanostores/react'
import { $defaultReasoningEffort } from '@/store/session'
import { useSessionView } from '../chat/session-view'
import { DEFAULT_REASONING_EFFORT } from '@hermes/shared'
import { paint, scenes, skyAt, type Phase, type Scene, type Sky, type Weather } from './scenes'
import './ambient.css'

const skies: Sky[] = ['Clock', 'Dawn', 'Day', 'Dusk', 'Night']
const weatherOptions: Weather[] = ['Clear', 'Cloudy', 'Rain', 'Snow', 'Wind']
type Preferences = {
  scene: Scene | 'off'
  sky: Sky
  weather: Weather
  moving: boolean
  reactive: boolean
  strength: number
}
const defaults: Preferences = {
  scene: 'soundings',
  sky: 'Clock',
  weather: 'Clear',
  moving: true,
  reactive: true,
  strength: 27
}
const key = 'theseus.ambient.v1'
function read(): Preferences {
  try {
    const raw = JSON.parse(localStorage.getItem(key) || '{}')
    return {
      scene: raw.scene === 'off' || scenes.some(s => s.id === raw.scene) ? raw.scene : defaults.scene,
      sky: skies.includes(raw.sky) ? raw.sky : defaults.sky,
      weather: weatherOptions.includes(raw.weather) ? raw.weather : defaults.weather,
      strength: Number.isFinite(raw.strength) ? Math.max(5, Math.min(65, Math.round(raw.strength))) : defaults.strength,
      moving: typeof raw.moving === 'boolean' ? raw.moving : true,
      reactive: typeof raw.reactive === 'boolean' ? raw.reactive : true
    }
  } catch {
    return defaults
  }
}
let preferences = read()
const subscribers = new Set<() => void>()
function emit() {
  subscribers.forEach(fn => fn())
}
function subscribe(fn: () => void) {
  subscribers.add(fn)
  return () => {
    subscribers.delete(fn)
  }
}
window.addEventListener('storage', e => {
  if (e.key === key || !e.key) {
    preferences = read()
    emit()
  }
})
function update(patch: Partial<Preferences>) {
  preferences = { ...preferences, ...patch }
  try {
    localStorage.setItem(key, JSON.stringify(preferences))
  } catch {
    /* Keep this window usable when storage is unavailable. */
  }
  emit()
}
function usePreferences() {
  return useSyncExternalStore(subscribe, () => preferences)
}

const effortGain: Record<string, number> = {
  none: 0,
  minimal: 0.12,
  low: 0.2,
  medium: 0.38,
  high: 0.62,
  xhigh: 0.82,
  max: 0.94,
  ultra: 1
}
export function activityLevel(phase: Phase, effort: string) {
  if (phase === 'Idle') return 0.08
  return Math.min(1, (phase === 'Waiting for model' ? 0.28 : 0.42) + (effortGain[effort] ?? 0.38) * 0.58)
}

function Canvas({
  scene,
  sky,
  weather,
  moving,
  energy,
  preview = false,
  strength = defaults.strength,
  phase = 'Idle'
}: {
  scene: Scene
  sky: Sky
  weather: Weather
  moving: boolean
  energy: number
  strength?: number
  preview?: boolean
  phase?: Phase
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  const time = useRef(1)
  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const reduce = matchMedia('(prefers-reduced-motion: reduce)')
    let timer = 0,
      last = 0,
      visible = true
    function draw() {
      window.clearTimeout(timer)
      if (!canvas || !ctx || !visible || document.hidden) {
        last = 0
        return
      }
      const w = canvas.clientWidth,
        h = canvas.clientHeight
      if (!w || !h) return
      const ratio = Math.min(devicePixelRatio || 1, 1.5)
      const pw = Math.round(w * ratio),
        ph = Math.round(h * ratio)
      if (canvas.width !== pw || canvas.height !== ph) {
        canvas.width = pw
        canvas.height = ph
      }
      const now = performance.now()
      const animate = moving && !reduce.matches
      if (animate && last)
        time.current += Math.min(0.2, (now - last) / 1000) * (0.52 + energy * 1.75) * (weather === 'Wind' ? 1.35 : 1)
      last = animate ? now : 0
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
      paint(
        ctx,
        w,
        h,
        scene,
        time.current,
        skyAt(sky, new Date().getHours()),
        weather,
        energy,
        document.documentElement.classList.contains('dark')
      )
      if (animate) {
        const interval = !document.hasFocus() ? 480 : !preview && energy === 0.08 ? 180 : 80
        timer = window.setTimeout(draw, interval)
      } else if (sky === 'Clock') timer = window.setTimeout(draw, 3600000 - (Date.now() % 3600000))
    }
    const size = new ResizeObserver(draw)
    const theme = new MutationObserver(draw)
    const visibility = new IntersectionObserver(entries => {
      visible = entries[0].isIntersecting
      draw()
    })
    size.observe(canvas)
    visibility.observe(canvas)
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme'] })
    window.addEventListener('focus', draw)
    window.addEventListener('blur', draw)
    document.addEventListener('visibilitychange', draw)
    reduce.addEventListener('change', draw)
    draw()
    return () => {
      window.clearTimeout(timer)
      size.disconnect()
      theme.disconnect()
      visibility.disconnect()
      window.removeEventListener('focus', draw)
      window.removeEventListener('blur', draw)
      document.removeEventListener('visibilitychange', draw)
      reduce.removeEventListener('change', draw)
    }
  }, [scene, sky, weather, moving, energy, preview])
  return (
    <canvas
      ref={ref}
      className={preview ? 'theseus-ambient-preview' : 'theseus-ambient-backdrop'}
      data-scene={scene}
      style={{ opacity: strength / 100 }}
      data-phase={phase}
      data-energy={energy}
      aria-hidden="true"
    />
  )
}

export function SessionAmbient({ busy, awaiting }: { busy: boolean; awaiting: boolean }) {
  const prefs = usePreferences()
  const view = useSessionView()
  const requested = useStore(view.$reasoningEffort)
  const wire = useStore(view.$reasoningEffortWire)
  const fallback = useStore($defaultReasoningEffort)
  const effort = wire || requested || fallback || DEFAULT_REASONING_EFFORT
  const phase: Phase = awaiting ? 'Waiting for model' : busy ? 'Working' : 'Idle'
  if (prefs.scene === 'off') return null
  return (
    <Canvas
      {...prefs}
      scene={prefs.scene}
      phase={phase}
      energy={prefs.reactive ? activityLevel(phase, effort) : 0.08}
    />
  )
}

function SceneSwatch({ scene, sky, weather }: { scene: Scene; sky: Sky; weather: Weather }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const ctx = ref.current?.getContext('2d')
    if (ctx) paint(ctx, 200, 66, scene, 2, skyAt(sky, new Date().getHours()), weather, 0.25, true)
  }, [scene, sky, weather])
  return <canvas width={200} height={66} ref={ref} className="theseus-scene-swatch" aria-hidden="true" />
}

export function AmbientSettings() {
  const prefs = usePreferences()
  return (
    <section className="theseus-atmosphere" aria-label="Conversation atmosphere">
      <h3>Conversation atmosphere</h3>
      {prefs.scene !== 'off' && (
        <div className="theseus-atmosphere-frame">
          <Canvas {...prefs} scene={prefs.scene} energy={0.38} preview />
        </div>
      )}
      <div className="theseus-scene-picker">
        {scenes.map(s => (
          <button
            key={s.id}
            aria-label={s.name}
            aria-pressed={prefs.scene === s.id}
            onClick={() => update({ scene: s.id })}
          >
            <SceneSwatch scene={s.id} sky={prefs.sky} weather={prefs.weather} />
            <strong>{s.name}</strong>
            <small>{s.detail}</small>
          </button>
        ))}
        <button
          aria-label="Background off"
          aria-pressed={prefs.scene === 'off'}
          onClick={() => update({ scene: 'off' })}
        >
          Off
        </button>
      </div>
      {prefs.scene !== 'off' && (
        <>
          <div className="theseus-scene-strength">
            <label htmlFor="theseus-art-strength">Background intensity</label>
            <input
              id="theseus-art-strength"
              type="range"
              min={5}
              max={65}
              value={prefs.strength}
              onChange={event => update({ strength: Number(event.target.value) })}
            />
            <output htmlFor="theseus-art-strength">{prefs.strength}%</output>
          </div>
          <fieldset>
            <legend>Light</legend>
            {skies.map(s => (
              <button
                key={s}
                aria-label={'Time ' + s}
                aria-pressed={prefs.sky === s}
                onClick={() => update({ sky: s })}
              >
                {s === 'Clock' ? 'Local clock' : s}
              </button>
            ))}
          </fieldset>
          <fieldset>
            <legend>Weather mood</legend>
            {weatherOptions.map(w => (
              <button
                key={w}
                aria-label={'Weather ' + w}
                aria-pressed={prefs.weather === w}
                onClick={() => update({ weather: w })}
              >
                {w}
              </button>
            ))}
          </fieldset>
          <div className="theseus-motion-options">
            <label>
              <input type="checkbox" checked={prefs.moving} onChange={e => update({ moving: e.target.checked })} />{' '}
              Animate background
            </label>
            <label>
              <input type="checkbox" checked={prefs.reactive} onChange={e => update({ reactive: e.target.checked })} />{' '}
              React to activity and reasoning effort
            </label>
          </div>
          <small>System Reduce Motion keeps the scene still.</small>
        </>
      )}
    </section>
  )
}
