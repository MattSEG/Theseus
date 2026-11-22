export const scenes = [
  { id: 'soundings', name: 'Soundings', detail: 'Bathymetric contours' },
  { id: 'orrery', name: 'Orrery', detail: 'Inclined orbital paths' },
  { id: 'ferry', name: 'Night Ferry', detail: 'Harbour lights and wakes' },
  { id: 'chamber', name: 'Cloud Chamber', detail: 'Condensation tracks' },
  { id: 'reeds', name: 'Reed Bed', detail: 'Wind through stems' },
  { id: 'glass', name: 'Rain Glass', detail: 'Droplets and ripples' },
  { id: 'loom', name: 'Loom', detail: 'Interlaced coloured threads' }
] as const
export type Scene = (typeof scenes)[number]['id']
export type Sky = 'Clock' | 'Dawn' | 'Day' | 'Dusk' | 'Night'
export type Weather = 'Clear' | 'Cloudy' | 'Rain' | 'Snow' | 'Wind'
export type Phase = 'Idle' | 'Waiting for model' | 'Working' | 'Responding'
const palettes = {
  Dawn: ['#d78373', '#efb875', '#91b8ad'],
  Day: ['#4d9d9b', '#84bda8', '#b6a064'],
  Dusk: ['#ae86aa', '#dc9777', '#667da9'],
  Night: ['#5795a7', '#9696ba', '#6ab4a6']
}
export function skyAt(sky: Sky, hour: number) {
  return sky !== 'Clock' ? sky : hour < 6 || hour >= 21 ? 'Night' : hour < 9 ? 'Dawn' : hour < 17 ? 'Day' : 'Dusk'
}
const tau = 2 * Math.PI
function n(i: number, seed = 1) {
  const v = Math.sin(i * 127.13 + seed * 75.37) * 43758.545
  return v - Math.floor(v)
}
type C = CanvasRenderingContext2D
function curve(c: C, start: number, end: number, inc: number, f: (u: number) => [number, number]) {
  c.beginPath()
  for (let u = start, i = 0; u <= end + 0.00001; u += inc, i++) {
    const [x, y] = f(u)
    if (i) c.lineTo(x, y)
    else c.moveTo(x, y)
  }
  c.stroke()
}
function dot(c: C, x: number, y: number, r: number) {
  c.beginPath()
  c.arc(x, y, r, 0, tau)
  c.fill()
}
export function paint(
  c: C,
  w: number,
  h: number,
  scene: Scene,
  t: number,
  sky: Exclude<Sky, 'Clock'>,
  weather: Weather,
  energy: number,
  dark: boolean
) {
  c.clearRect(0, 0, w, h)
  const s = Math.min(w, h)
  if (!s) return
  c.save()
  c.lineCap = 'round'
  c.lineJoin = 'round'
  const pal = palettes[sky]
  const wind = weather === 'Wind' ? 1.85 : weather === 'Cloudy' ? 0.72 : 1
  const tint = (i: number, a: number) => {
    c.strokeStyle = pal[i % 3]
    c.fillStyle = pal[i % 3]
    c.globalAlpha = Math.min(1, a * (dark ? 1 : 0.95))
  }
  // A coloured wash lives at the perimeter, leaving the text column quiet.
  const halo = c.createRadialGradient(w * 0.85, h * 0.92, 0, w * 0.85, h * 0.92, w * 0.62)
  halo.addColorStop(0, pal[0])
  halo.addColorStop(1, 'transparent')
  c.globalAlpha = weather === 'Cloudy' ? 0.24 : 0.15
  c.fillStyle = halo
  c.fillRect(0, 0, w, h)
  if (scene === 'soundings') {
    for (let i = 0; i < 24; i++) {
      const r = s * (0.075 + i * 0.028)
      tint(i % 6 === 0 ? 1 : 0, i % 6 === 0 ? 0.8 : 0.44)
      c.lineWidth = i % 6 === 0 ? 1.5 : 1
      curve(c, 0, tau, tau / 150, a => {
        const m =
          1 +
          0.19 * Math.sin(3 * a + t * 0.085) +
          0.085 * Math.cos(5 * a - t * 0.093 + i * 0.055) +
          0.06 * Math.sin(2 * a + i * 0.08)
        return [w * 0.81 + Math.cos(a) * r * m * 1.55, h * 0.87 + Math.sin(a) * r * m]
      })
    }
  } else if (scene === 'orrery') {
    const x = w * 0.77,
      y = h * 0.73
    for (let i = 0; i < 8; i++) {
      const r = s * (0.1 + i * 0.075),
        ang = -0.39 + i * 0.047
      const pos = (a: number): [number, number] => [
        x + r * (Math.cos(a) * Math.cos(ang) - 0.48 * Math.sin(a) * Math.sin(ang)),
        y + r * (Math.cos(a) * Math.sin(ang) + 0.48 * Math.sin(a) * Math.cos(ang))
      ]
      tint(i, 0.5)
      c.lineWidth = 1
      curve(c, 0, tau, tau / 136, pos)
      const [px, py] = pos(n(i) * tau + t * (0.028 + i * 0.005) * (i % 2 === 0 ? 1 : -1))
      tint(i + 1, 0.95)
      dot(c, px, py, Math.max(1.3, s * (0.003 + i * 0.0004)))
    }
    tint(1, 0.7)
    dot(c, x, y, s * 0.011)
  } else if (scene === 'ferry') {
    const y = h * 0.74
    tint(2, 0.25)
    c.beginPath()
    c.moveTo(0, y)
    for (let i = 0; i <= 100; i++) {
      const x = (i * w) / 100
      c.lineTo(x, y - s * 0.09 * (0.55 + 0.38 * Math.sin(i * 0.087) + 0.2 * Math.sin(i * 0.142)))
    }
    c.lineTo(w, y)
    c.closePath()
    c.fill()
    for (let i = 0; i < 34; i++) {
      const cy = y + n(i) * h * 0.25,
        x = n(i, 3) * w + Math.sin(t * 0.17 + n(i) * tau) * s * 0.025
      tint(i % 4 === 0 ? 1 : 0, 0.32 + n(i, 7) * 0.4)
      c.lineWidth = 1 + s * 0.0008
      curve(c, 0, 1, 0.05, u => [x + u * s * (0.05 + n(i, 5) * 0.16), cy + Math.sin(u * 5 + t * 0.3 + i) * 1.5])
    }
    const fx = w * (0.5 + 0.38 * Math.sin(t * 0.038 + 0.4))
    tint(1, 0.92)
    c.beginPath()
    c.moveTo(fx - s * 0.031, y - s * 0.008)
    c.lineTo(fx + s * 0.03, y - s * 0.008)
    c.lineTo(fx + s * 0.019, y)
    c.lineTo(fx - s * 0.021, y)
    c.closePath()
    c.fill()
    c.fillRect(fx - s * 0.016, y - s * 0.018, s * 0.032, s * 0.01)
    dot(c, fx + s * 0.02, y - s * 0.018, 1.8)
  } else if (scene === 'chamber') {
    for (let i = 0; i < 22; i++) {
      const age = (t * (0.024 + n(i, 5) * 0.022) + n(i)) % 1
      const fade = Math.sin(Math.PI * age)
      tint(i, 0.8 * fade)
      c.lineWidth = 0.6 + s * 0.001
      const x = n(i, 4) * w,
        y = n(i, 3) * h,
        angle = n(i, 9) * tau,
        r = s * (0.08 + 0.43 * n(i, 11))
      c.setLineDash(i % 3 ? [2, 3 + n(i) * 4] : [])
      curve(c, 0, 1, 0.025, u => {
        const a = angle + u * (1 + energy) * 1.25
        return [x + (Math.sin(a) - Math.sin(angle)) * r, y + (Math.cos(a) - Math.cos(angle)) * r + age * s * 0.035]
      })
    }
    c.setLineDash([])
  } else if (scene === 'reeds') {
    for (let i = 0; i < 74; i++) {
      const x = n(i) * w,
        height = s * (0.06 + n(i, 3) ** 2 * 0.42) * (x / w > 0.35 && x / w < 0.65 ? 0.53 : 1),
        y = h + 3 + n(i, 7) * s * 0.04
      const lean = height * (0.13 + 0.09 * Math.sin(t * 0.26 * wind + (x / w) * 5) + 0.055 * Math.sin(t * 0.46 + i))
      const tipX = x + lean,
        tipY = y - height
      tint(i % 6 === 0 ? 1 : i % 2 ? 0 : 2, 0.33 + 0.55 * n(i, 4))
      c.lineWidth = 0.9 + s * 0.0014
      c.beginPath()
      c.moveTo(x, y)
      c.quadraticCurveTo(x - lean * 0.25, y - height * 0.52, tipX, tipY)
      c.stroke()
      c.beginPath()
      c.ellipse(tipX, tipY, height * 0.012 + 1, height * 0.062, Math.atan2(lean, height), 0, tau)
      c.fill()
    }
  } else if (scene === 'glass') {
    for (let i = 0; i < 40; i++) {
      const u = (t * (0.021 + n(i, 4) * 0.04) + n(i)) % 1,
        x = w * n(i, 7),
        y = h * u
      const fade = Math.sin(Math.PI * u)
      tint(i, 0.64 * fade)
      c.lineWidth = 1 + n(i, 3) * 1.6
      c.beginPath()
      c.moveTo(x, y - s * 0.045 * u)
      c.quadraticCurveTo(x + 2, y - s * 0.011, x, y)
      c.stroke()
      dot(c, x, y, 1.6 + n(i, 3) * 1.8)
      const age = (t * 0.12 + n(i, 8)) % 1
      if (i < 12) {
        tint(i, 0.44 * (1 - age))
        c.beginPath()
        c.ellipse(x, h * (0.78 + 0.18 * n(i)), s * (0.006 + age * 0.06), s * (0.002 + age * 0.014), 0, 0, tau)
        c.stroke()
      }
    }
  } else {
    for (let i = 0; i < 30; i++) {
      const x = (w * (i - 6)) / 20
      tint(i % 5 === 0 ? 1 : 2, 0.32)
      c.lineWidth = i % 5 === 0 ? 2 : 1
      curve(c, 0, 1, 0.023, u => [x + w * 0.52 * u + s * 0.04 * Math.sin(u * 7 + t * 0.14 + i * 0.09), h * u])
    }
    for (let i = 0; i < 22; i++) {
      const y = h * (0.35 + i * 0.035)
      tint(i % 7 === 0 ? 1 : 0, 0.54)
      c.lineWidth = i % 7 === 0 ? 2 : 1
      curve(c, 0, 1, 0.02, u => [
        u * w,
        y - s * 0.11 * Math.sin(4 * u + t * 0.19 + i * 0.055) + s * 0.024 * Math.sin(12 * u - t * 0.13)
      ])
    }
  }
  if (weather === 'Rain' || weather === 'Snow') {
    for (let i = 0; i < 22; i++) {
      const u = (n(i, 13) + t * (weather === 'Snow' ? 0.012 : 0.065)) % 1,
        x = w * n(i, 2) + Math.sin(t * 0.18 + i) * s * 0.025,
        y = u * h
      tint(2, 0.48 * Math.sin(u * Math.PI))
      if (weather === 'Snow') dot(c, x, y, 1 + n(i) * 1.4)
      else {
        c.lineWidth = 1
        c.beginPath()
        c.moveTo(x, y)
        c.lineTo(x - 2, y + 7)
        c.stroke()
      }
    }
  }
  c.restore()
}
