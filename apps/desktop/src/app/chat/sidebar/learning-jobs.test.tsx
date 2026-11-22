import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { LearningJobs } from './learning-jobs'

vi.mock('@/lib/desktop-fs', () => ({ isDesktopFsRemoteMode: () => false }))

const { preview } = vi.hoisted(() => ({ preview: vi.fn() }))
vi.mock('@/store/preview', () => ({ openPreview: preview }))

it('opens the local investigation journal for the configured profile', () => {
  render(<LearningJobs directory="/mini/theseus/learning-runs" />)
  fireEvent.click(screen.getByRole('button', { name: 'Investigations' }))
  expect(preview).toHaveBeenCalledWith(expect.objectContaining({ path: '/mini/theseus/project-recall/investigations.html' }))
})

const original = window.hermesDesktop
afterEach(() => {
  cleanup()
  window.hermesDesktop = original
})

it('keeps a newer decision when a previous read finishes late and releases the watch', async () => {
  const path = '/trial/index.json'
  const json = (phase: string) => ({ path, text: JSON.stringify([{ run: 1, decision: null, problem: 'Print', phase }]) })
  let changed: (event: { path: string }) => void = () => {}
  let finish: (result: ReturnType<typeof json>) => void = () => {}
  const slow = new Promise<ReturnType<typeof json>>(resolve => { finish = resolve })
  const readFileText = vi.fn().mockReturnValueOnce(slow).mockResolvedValue(json('proposal'))
  const stop = vi.fn()
  const unsubscribe = vi.fn()
  window.hermesDesktop = {
    ...original, readFileText, watchPreviewFile: vi.fn().mockResolvedValue({ id: 'index' }),
    stopPreviewFileWatch: stop,
    onPreviewFileChanged: handler => {
      changed = event => handler({ ...event, id: 'index', url: 'file://' + path })
      return unsubscribe
    }
  }
  render(<LearningJobs directory="/trial" />)
  const toggle = screen.getByRole('button', { name: 'Learning jobs' })
  fireEvent.click(toggle)
  await screen.findByText(/Proposed/)
  readFileText.mockResolvedValue(json('evaluation'))
  await act(async () => changed({ path }))
  await act(async () => finish(json('baseline')))
  expect(screen.getByText(/Awaiting review/)).toBeTruthy()
  const search = screen.getByRole('searchbox', { name: 'Search learning jobs' })
  fireEvent.change(search, { target: { value: ' missing ' } })
  expect(screen.getByText('No matching learning jobs.')).toBeTruthy()
  fireEvent.change(search, { target: { value: ' AWAITING REVIEW ' } })
  expect(screen.getByRole('button', { name: /Print · Run 1/ })).toBeTruthy()
  fireEvent.change(search, { target: { value: 'unseen-case' } })
  readFileText.mockResolvedValue({ path, text: JSON.stringify([
    { job: 'unseen-case', problem: 'Print', run: 2, phase: 'baseline', decision: null },
    { problem: 'Print', run: 1, phase: 'evaluation', decision: null }
  ]) })
  await act(async () => changed({ path }))
  expect(screen.getByRole('button', { name: /unseen-case · Print · Run 2/ })).toBeTruthy()
  expect(screen.queryByText(/Awaiting review/)).toBeNull()
  readFileText.mockResolvedValue({ path, text: JSON.stringify([
    { job: 'failed-proof', problem: 'Print', run: 4, phase: 'evaluation', decision: null, recommendation: 'revert' },
    { job: 'reviewed-proof', problem: 'Print', run: 3, phase: 'review', decision: 'revert', recommendation: 'keep' }
  ]) })
  await act(async () => changed({ path }))
  fireEvent.change(search, { target: { value: 'recommends revert' } })
  expect(screen.getByRole('button', { name: /failed-proof.*Awaiting review · Evaluator recommends revert/ })).toBeTruthy()
  expect(screen.queryByRole('button', { name: /reviewed-proof/ })).toBeNull()
  fireEvent.change(search, { target: { value: 'human decision' } })
  expect(screen.getByRole('button', { name: /reviewed-proof.*Human decision · revert/ })).toBeTruthy()
  fireEvent.click(toggle)
  expect(stop).toHaveBeenCalledWith('index')
  expect(unsubscribe).toHaveBeenCalledOnce()
})
