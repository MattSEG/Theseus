// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { activityLevel } from './ambient'
import { skyAt } from './scenes'

describe('conversation atmosphere', () => {
  it('raises active motion with effort and settles to the same idle level', () => {
    const levels = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra']
    const waiting = levels.map(level => activityLevel('Waiting for model', level))
    const working = levels.map(level => activityLevel('Working', level))
    for (let index = 1; index < levels.length; index++) {
      expect(waiting[index]).toBeGreaterThan(waiting[index - 1])
      expect(working[index]).toBeGreaterThan(working[index - 1])
    }
    expect(activityLevel('Idle', 'low')).toBe(activityLevel('Idle', 'ultra'))
    expect(activityLevel('Working', '')).toBe(activityLevel('Working', 'medium'))
  })

  it('resolves daily transitions and preserves a manually selected light', () => {
    expect([5, 6, 9, 17, 21].map(hour => skyAt('Clock', hour))).toEqual(['Night', 'Dawn', 'Day', 'Dusk', 'Night'])
    expect(skyAt('Dawn', 23)).toBe('Dawn')
  })
})
