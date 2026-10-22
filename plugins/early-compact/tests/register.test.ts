import type { On } from 'claude-code'
import { describe, expect, mock, test } from 'claude-code/testing'

const TOOL = 'mcp__early-compact__compact_while_waiting'
const START = { cwd: '/w', surface: 'terminal', isInteractive: true } as const
const AUTO = { trigger: 'auto', messages: [{ role: 'user', text: 'hi', toolUses: [] }], instructions: 'auto' } as const
const TURN = { answer: 'waiting', durationMs: 1, isAborted: false, turnId: 't1', reason: 'answer' } as const

type Outcome = 'ok' | 'skip' | 'reject'

// Stands for the engine. `outcomes` answers each compact() in turn; the last repeats.
// `toolMs` is how long each tool call takes; `usage.percent` can be changed between calls.
function world(on: On, percent: number, outcomes: Outcome[] = ['ok'], toolMs = 0) {
  const usage = { percent }
  const compactions: (string | undefined)[] = []
  const toasts: string[] = []
  const registered: string[] = []
  const clock = mock.clock(on)
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('tool.register', ($, e) => {
    registered.push(e.name)
    return { value: { tool: `mcp__early-compact__${e.name}` } }
  })
  on('session.usage', () => ({ value: { startedAt: 0, context: { window: 200_000, percent: usage.percent }, rateLimits: [] } }))
  on('turn.complete', ($, e) => ({ text: e.answer }))
  on('tool.call', async () => {
    if (toolMs > 0) await clock.set(clock.now() + toolMs)
    return { result: {} }
  })
  on('session.compact', ($, e) => {
    compactions.push(e.instructions)
    const o = outcomes[Math.min(compactions.length - 1, outcomes.length - 1)]
    if (o === 'reject') throw new Error('a turn is running')
    if (o === 'skip') return { skip: 'vetoed' }
    return { messages: [{ role: 'user', text: 'summary', toolUses: [] }] }
  })
  on('ui.status', () => ({ value: undefined }))
  on('ui.toast', ($, e) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  return { clock, compactions, toasts, registered, usage }
}

describe('scheduling', () => {
  test('registers the tool on session start', async ($, on) => {
    const { registered } = world(on, 70)
    await $.session.start(START)
    expect(registered).toEqual(['compact_while_waiting'])
  })

  test('above the threshold the tool schedules one compaction at turn end', async ($, on) => {
    const { clock, compactions, toasts } = world(on, 70)
    await $.session.start(START)
    const r = await $.tool.call({ tool: TOOL, instructions: 'task b1 runs make' })
    expect(String(r.result)).toContain('scheduled')
    expect(compactions).toEqual([])

    await $.turn.complete(TURN)
    await clock.settle()
    expect(compactions).toEqual(['task b1 runs make'])
    expect(toasts).toEqual(['early-compact: compacted while waiting'])

    await $.turn.complete({ ...TURN, turnId: 't2' })
    await clock.advance(10_000)
    expect(compactions).toHaveLength(1)
  })

  test('below the threshold nothing is scheduled', async ($, on) => {
    const { clock, compactions } = world(on, 20)
    await $.session.start(START)
    const r = await $.tool.call({ tool: TOOL, instructions: 'x' })
    expect(String(r.result)).toContain('not needed')
    await $.turn.complete(TURN)
    await clock.advance(1000)
    expect(compactions).toEqual([])
  })

  test('the threshold option is honoured', { options: { threshold: 30 } }, async ($, on) => {
    const { clock, compactions } = world(on, 40)
    await $.session.start(START)
    const r = await $.tool.call({ tool: TOOL, instructions: 'x' })
    expect(String(r.result)).toContain('scheduled')
    await $.turn.complete(TURN)
    await clock.settle()
    expect(compactions).toEqual(['x'])
  })

  test('a later call replaces earlier instructions', async ($, on) => {
    const { clock, compactions } = world(on, 70)
    await $.session.start(START)
    await $.tool.call({ tool: TOOL, instructions: 'first' })
    await $.tool.call({ tool: TOOL, instructions: 'second' })
    await $.turn.complete(TURN)
    await clock.settle()
    expect(compactions).toEqual(['second'])
  })
})

describe('another compaction first', () => {
  test('a compaction between scheduling and turn end drops the schedule', async ($, on) => {
    const { clock, compactions, toasts } = world(on, 80)
    await $.session.start(START)
    await $.tool.call({ tool: TOOL, instructions: 'x' })
    await $.session.compact(AUTO)
    await $.turn.complete(TURN)
    await clock.advance(10_000)
    expect(compactions).toEqual(['auto'])
    expect(toasts).toEqual(['early-compact: skipped (already compacted)'])
  })

  test('a compaction while retrying stops the retries', async ($, on) => {
    const { clock, compactions } = world(on, 80, ['reject', 'ok'])
    await $.session.start(START)
    await $.tool.call({ tool: TOOL, instructions: 'x' })
    await $.turn.complete(TURN)
    await clock.advance(0)
    expect(compactions).toEqual(['x'])
    await $.session.compact(AUTO)
    await clock.advance(10_000)
    expect(compactions).toEqual(['x', 'auto'])
  })

  test('a skipped compaction does not count', async ($, on) => {
    const { clock, compactions } = world(on, 80, ['skip', 'ok'])
    await $.session.start(START)
    await $.tool.call({ tool: TOOL, instructions: 'x' })
    await $.session.compact(AUTO)
    await $.turn.complete(TURN)
    await clock.settle()
    expect(compactions).toEqual(['auto', 'x'])
  })
})

describe('subagents and interrupted turns', () => {
  test('the tool refuses inside a subagent', async ($, on) => {
    const { clock, compactions } = world(on, 70)
    await $.session.start(START)
    const r = await $.tool.call({ tool: TOOL, instructions: 'x', agentId: 'a1' })
    expect(String(r.result)).toContain('subagents')
    await $.turn.complete(TURN)
    await clock.advance(1000)
    expect(compactions).toEqual([])
  })

  test("a subagent's turn end does not consume the schedule", async ($, on) => {
    const { clock, compactions } = world(on, 70)
    await $.session.start(START)
    await $.tool.call({ tool: TOOL, instructions: 'x' })
    await $.turn.complete({ ...TURN, agentId: 'a1' })
    await clock.advance(1000)
    expect(compactions).toEqual([])
    await $.turn.complete(TURN)
    await clock.settle()
    expect(compactions).toEqual(['x'])
  })

  for (const reason of ['aborted', 'error'] as const) {
    test(`a turn ending ${reason} drops the schedule`, async ($, on) => {
      const { clock, compactions } = world(on, 70)
      await $.session.start(START)
      await $.tool.call({ tool: TOOL, instructions: 'x' })
      await $.turn.complete({ ...TURN, reason, isAborted: reason === 'aborted' })
      await $.turn.complete({ ...TURN, turnId: 't2' })
      await clock.advance(10_000)
      expect(compactions).toEqual([])
    })
  }
})

describe('when compact() fails', () => {
  test('a skip is reported', async ($, on) => {
    const { clock, compactions, toasts } = world(on, 70, ['skip'])
    await $.session.start(START)
    await $.tool.call({ tool: TOOL, instructions: 'x' })
    await $.turn.complete(TURN)
    await clock.settle()
    expect(compactions).toHaveLength(1)
    expect(toasts).toEqual(['early-compact: skipped (vetoed)'])
  })

  test('a rejection is retried', async ($, on) => {
    const { clock, compactions, toasts } = world(on, 70, ['reject', 'reject', 'ok'])
    await $.session.start(START)
    await $.tool.call({ tool: TOOL, instructions: 'x' })
    await $.turn.complete(TURN)
    await clock.advance(1000)
    expect(compactions).toHaveLength(3)
    expect(toasts).toEqual(['early-compact: compacted while waiting'])
  })

  test('after the retries it waits for the next turn end', async ($, on) => {
    const outcomes: Outcome[] = [...Array(9).fill('reject'), 'ok']
    const { clock, compactions, toasts } = world(on, 70, outcomes)
    await $.session.start(START)
    await $.tool.call({ tool: TOOL, instructions: 'x' })
    await $.turn.complete(TURN)
    await clock.advance(10_000)
    expect(compactions).toHaveLength(9)
    expect(toasts).toEqual([])

    await $.turn.complete({ ...TURN, turnId: 't2' })
    await clock.settle()
    expect(compactions).toHaveLength(10)
    expect(toasts).toEqual(['early-compact: compacted while waiting'])
  })

  test('it gives up after one more turn end', async ($, on) => {
    const { clock, compactions, toasts } = world(on, 70, ['reject'])
    await $.session.start(START)
    await $.tool.call({ tool: TOOL, instructions: 'x' })
    for (const turnId of ['t1', 't2', 't3']) {
      await $.turn.complete({ ...TURN, turnId })
      await clock.advance(10_000)
    }
    expect(compactions).toHaveLength(18)
    expect(toasts).toHaveLength(1)
    expect(toasts[0]).toContain('gave up')
  })

  test('a new schedule made while retrying wins', async ($, on) => {
    const outcomes: Outcome[] = [...Array(9).fill('reject'), 'ok']
    const { clock, compactions } = world(on, 70, outcomes)
    await $.session.start(START)
    await $.tool.call({ tool: TOOL, instructions: 'old' })
    await $.turn.complete(TURN)
    await clock.advance(500)
    await $.tool.call({ tool: TOOL, instructions: 'new' })
    await clock.advance(10_000)
    await $.turn.complete({ ...TURN, turnId: 't2' })
    await clock.settle()
    expect(compactions.at(-1)).toBe('new')
  })
})

describe('guidance above the threshold', () => {
  test('the first tool call above the threshold carries a reminder, once', async ($, on) => {
    const { usage } = world(on, 70)
    await $.session.start(START)
    const r1 = await $.tool.call({ tool: 'Read', file_path: '/w/a' })
    expect(r1.context?.[0]).toContain('run_in_background: true')
    const r2 = await $.tool.call({ tool: 'Read', file_path: '/w/a' })
    expect(r2.context).toBeUndefined()

    usage.percent = 20
    const r3 = await $.tool.call({ tool: 'Read', file_path: '/w/a' })
    expect(r3.context).toBeUndefined()
    usage.percent = 65
    const r4 = await $.tool.call({ tool: 'Read', file_path: '/w/a' })
    expect(r4.context?.[0]).toContain('65% full')
  })

  test('below the threshold there is no reminder', async ($, on) => {
    world(on, 20)
    await $.session.start(START)
    const r = await $.tool.call({ tool: 'Read', file_path: '/w/a' })
    expect(r.context).toBeUndefined()
  })

  test('subagent calls carry no reminder', async ($, on) => {
    world(on, 70)
    await $.session.start(START)
    const r = await $.tool.call({ tool: 'mcp__other__x', agentId: 'a1' })
    expect(r.context).toBeUndefined()
  })
})

describe('missed chance', () => {
  test('a slow foreground Bash above the threshold toasts and tells the model', async ($, on) => {
    const { toasts } = world(on, 70, ['ok'], 20_000)
    await $.session.start(START)
    await $.tool.call({ tool: 'Read', file_path: '/w/a' })
    const r = await $.tool.call({ tool: 'Bash', command: 'make' })
    expect(r.context?.[0]).toContain('ran 20s in the foreground')
    expect(toasts).toEqual(['early-compact: missed chance, 20s foreground Bash at 70% context'])
  })

  test('background or quick calls do not', async ($, on) => {
    const { toasts } = world(on, 70, ['ok'], 20_000)
    await $.session.start(START)
    await $.tool.call({ tool: 'Bash', command: 'make', run_in_background: true })
    const r = await $.tool.call({ tool: 'Read', file_path: '/w/a' })
    expect(r.context).toBeUndefined()
    expect(toasts).toEqual([])
  })

  test('quick foreground calls do not', async ($, on) => {
    const { toasts } = world(on, 70, ['ok'], 5_000)
    await $.session.start(START)
    await $.tool.call({ tool: 'Bash', command: 'ls' })
    const r = await $.tool.call({ tool: 'Bash', command: 'ls' })
    expect(r.context).toBeUndefined()
    expect(toasts).toEqual([])
  })

  test('the nudge option turns the toast off, not the guidance', { options: { nudge: false } }, async ($, on) => {
    const { toasts } = world(on, 70, ['ok'], 20_000)
    await $.session.start(START)
    await $.tool.call({ tool: 'Read', file_path: '/w/a' })
    const r = await $.tool.call({ tool: 'Bash', command: 'make' })
    expect(r.context?.[0]).toContain('in the foreground')
    expect(toasts).toEqual([])
  })
})
