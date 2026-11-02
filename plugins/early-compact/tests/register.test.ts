import type { On } from 'claude-code'
import { describe, expect, mock, test, type Engine } from 'claude-code/testing'

const AUTO = { trigger: 'auto' as const, messages: [{ role: 'user' as const, text: 'hi', toolUses: [] }], instructions: 'auto' }
const TURN = { answer: 'waiting', durationMs: 1, isAborted: false, turnId: 't1', reason: 'answer' } as const
const BG = { tool: 'Bash', command: 'make test', description: 'Run tests', run_in_background: true } as const

type Outcome = 'ok' | 'skip' | 'reject'

// Stands for the engine. `outcomes` answers each compact() in turn; the last repeats.
// `toolMs` is how long each tool call takes; `usage.percent` can be changed between calls.
// A backgrounded Bash call gets task ids b1, b2, ...
function world(on: On, percent: number, outcomes: Outcome[] = ['ok'], toolMs = 0, bgExtra: Record<string, unknown> = {}) {
  const usage = { percent }
  const compactions: (string | undefined)[] = []
  const toasts: string[] = []
  const clock = mock.clock(on)
  const session = mock.session(on)
  let tasks = 0
  on('session.usage', () => ({ value: { startedAt: 0, context: { window: 200_000, percent: usage.percent }, rateLimits: [] } }))
  on('turn.complete', ($, e) => ({ text: e.answer }))
  on('tool.call', async ($, e) => {
    if (toolMs > 0) await clock.set(clock.now() + toolMs)
    const bg = e.tool === 'Bash' && e.run_in_background === true
    return { result: { stdout: '', stderr: '', interrupted: false, ...(bg ? { backgroundTaskId: `b${++tasks}`, ...bgExtra } : {}) } }
  })
  on('session.start', ($, e) => ({ cwd: e.cwd }))
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
  const notices = () =>
    session
      .appended()
      .filter(r => r.message.type === 'system')
      .map(r => r.message.content.map(b => (b.type === 'text' ? b.text : '')).join(''))
  return { clock, compactions, toasts, usage, notices }
}

// The row the engine stores when a background task's notification is queued.
const finished = ($: Engine, id: string) =>
  $.session.append({
    message: {
      type: 'user',
      role: 'user',
      isMeta: true,
      content: [{ type: 'text', text: `<task-notification>\n<task-id>${id}</task-id>\n<status>completed</status>\n</task-notification>` }],
    },
    door: 'queue',
    origin: { kind: 'task-notification' },
    uuid: `notification-${id}`,
  })

describe('scheduling', () => {
  test('a background Bash above the threshold schedules one compaction at turn end', async ($, on) => {
    const { clock, compactions, notices } = world(on, 75)
    const r = await $.tool.call(BG)
    expect(r.context?.[0]).toContain('compaction scheduled while background task b1 runs')
    expect(compactions).toEqual([])

    await $.turn.complete(TURN)
    await clock.settle()
    expect(compactions).toHaveLength(1)
    expect(compactions[0]).toContain('b1 (Run tests)')
    expect(notices()).toEqual(['early-compact: compacted while waiting'])

    await $.turn.complete({ ...TURN, turnId: 't2' })
    await clock.advance(10_000)
    expect(compactions).toHaveLength(1)
  })

  test('below the threshold nothing is scheduled', async ($, on) => {
    const { clock, compactions } = world(on, 50)
    const r = await $.tool.call(BG)
    expect(r.context).toBeUndefined()
    await $.turn.complete(TURN)
    await clock.advance(1000)
    expect(compactions).toEqual([])
  })

  test('the threshold option is honoured', { options: { threshold: 30 } }, async ($, on) => {
    const { clock, compactions } = world(on, 40)
    await $.tool.call(BG)
    await $.turn.complete(TURN)
    await clock.settle()
    expect(compactions).toHaveLength(1)
  })

  test('foreground Bash and other tools schedule nothing', async ($, on) => {
    const { clock, compactions } = world(on, 75)
    await $.tool.call({ tool: 'Bash', command: 'ls' })
    await $.tool.call({ tool: 'Read', file_path: '/w/a' })
    await $.turn.complete(TURN)
    await clock.advance(1000)
    expect(compactions).toEqual([])
  })

  test('a second background task joins the same compaction', async ($, on) => {
    const { clock, compactions } = world(on, 75)
    await $.tool.call(BG)
    const r = await $.tool.call({ ...BG, description: 'Build' })
    expect(r.context?.[0]).toContain('also waits on background task b2')
    await $.turn.complete(TURN)
    await clock.settle()
    expect(compactions).toHaveLength(1)
    expect(compactions[0]).toContain('b1 (Run tests); b2 (Build)')
  })
})

describe('tasks that finish first', () => {
  test('a task finished before turn end skips the compaction', async ($, on) => {
    const { clock, compactions, notices } = world(on, 75)
    await $.tool.call(BG)
    await finished($, 'b1')
    await $.turn.complete(TURN)
    await clock.advance(10_000)
    expect(compactions).toEqual([])
    expect(notices()).toEqual(['early-compact: skipped (background task already finished)'])
  })

  test('one of two tasks still running keeps it, naming only that one', async ($, on) => {
    const { clock, compactions } = world(on, 75)
    await $.tool.call(BG)
    await $.tool.call({ ...BG, description: 'Build' })
    await finished($, 'b1')
    await $.turn.complete(TURN)
    await clock.settle()
    expect(compactions).toHaveLength(1)
    expect(compactions[0]).not.toContain('b1')
    expect(compactions[0]).toContain('b2 (Build)')
  })

  test('a rearmed compaction is skipped once its task has finished', async ($, on) => {
    const { clock, compactions, notices } = world(on, 75, ['reject'])
    await $.tool.call(BG)
    await $.turn.complete(TURN)
    await clock.advance(10_000)
    expect(compactions).toHaveLength(9)
    await finished($, 'b1')
    await $.turn.complete({ ...TURN, turnId: 't2' })
    await clock.advance(10_000)
    expect(compactions).toHaveLength(9)
    expect(notices()).toEqual(['early-compact: skipped (background task already finished)'])
  })
})

describe('another compaction first', () => {
  test('a compaction between scheduling and turn end drops the schedule', async ($, on) => {
    const { clock, compactions, notices } = world(on, 80)
    await $.tool.call(BG)
    await $.session.compact(AUTO)
    await $.turn.complete(TURN)
    await clock.advance(10_000)
    expect(compactions).toEqual(['auto'])
    expect(notices()).toEqual(['early-compact: skipped (already compacted)'])
  })

  test('a background task after that compaction schedules afresh', async ($, on) => {
    const { clock, compactions } = world(on, 80)
    await $.tool.call(BG)
    await $.session.compact(AUTO)
    const r = await $.tool.call(BG)
    expect(r.context?.[0]).toContain('compaction scheduled while background task b2 runs')
    await $.turn.complete(TURN)
    await clock.settle()
    expect(compactions).toHaveLength(2)
  })

  test('a compaction while retrying stops the retries', async ($, on) => {
    const { clock, compactions } = world(on, 80, ['reject', 'ok'])
    await $.tool.call(BG)
    await $.turn.complete(TURN)
    await clock.advance(0)
    expect(compactions).toHaveLength(1)
    await $.session.compact(AUTO)
    await clock.advance(10_000)
    expect(compactions).toHaveLength(2)
    expect(compactions[1]).toBe('auto')
  })

  test('a skipped compaction does not count', async ($, on) => {
    const { clock, compactions } = world(on, 80, ['skip', 'ok'])
    await $.tool.call(BG)
    await $.session.compact(AUTO)
    await $.turn.complete(TURN)
    await clock.settle()
    expect(compactions).toHaveLength(2)
  })
})

describe('subagents and interrupted turns', () => {
  test('a subagent background Bash schedules nothing', async ($, on) => {
    const { clock, compactions } = world(on, 75)
    await $.tool.call({ ...BG, agentId: 'a1' })
    await $.turn.complete(TURN)
    await clock.advance(1000)
    expect(compactions).toEqual([])
  })

  test("a subagent's turn end does not consume the schedule", async ($, on) => {
    const { clock, compactions } = world(on, 75)
    await $.tool.call(BG)
    await $.turn.complete({ ...TURN, agentId: 'a1' })
    await clock.advance(1000)
    expect(compactions).toEqual([])
    await $.turn.complete(TURN)
    await clock.settle()
    expect(compactions).toHaveLength(1)
  })

  for (const reason of ['aborted', 'error'] as const) {
    test(`a turn ending ${reason} drops the schedule`, async ($, on) => {
      const { clock, compactions } = world(on, 75)
      await $.tool.call(BG)
      await $.turn.complete({ ...TURN, reason, isAborted: reason === 'aborted' })
      await $.turn.complete({ ...TURN, turnId: 't2' })
      await clock.advance(10_000)
      expect(compactions).toEqual([])
    })
  }
})

describe('when compact() fails', () => {
  test('a skip is reported', async ($, on) => {
    const { clock, compactions, notices } = world(on, 75, ['skip'])
    await $.tool.call(BG)
    await $.turn.complete(TURN)
    await clock.settle()
    expect(compactions).toHaveLength(1)
    expect(notices()).toEqual(['early-compact: skipped (vetoed)'])
  })

  test('a rejection is retried', async ($, on) => {
    const { clock, compactions, notices } = world(on, 75, ['reject', 'reject', 'ok'])
    await $.tool.call(BG)
    await $.turn.complete(TURN)
    await clock.advance(1000)
    expect(compactions).toHaveLength(3)
    expect(notices()).toEqual(['early-compact: compacted while waiting'])
  })

  test('after the retries it waits for the next turn end', async ($, on) => {
    const outcomes: Outcome[] = [...Array(9).fill('reject'), 'ok']
    const { clock, compactions, notices } = world(on, 75, outcomes)
    await $.tool.call(BG)
    await $.turn.complete(TURN)
    await clock.advance(10_000)
    expect(compactions).toHaveLength(9)
    expect(notices()).toEqual([])

    await $.turn.complete({ ...TURN, turnId: 't2' })
    await clock.settle()
    expect(compactions).toHaveLength(10)
    expect(notices()).toEqual(['early-compact: compacted while waiting'])
  })

  test('it gives up after one more turn end', async ($, on) => {
    const { clock, compactions, notices } = world(on, 75, ['reject'])
    await $.tool.call(BG)
    for (const turnId of ['t1', 't2', 't3']) {
      await $.turn.complete({ ...TURN, turnId })
      await clock.advance(10_000)
    }
    expect(compactions).toHaveLength(18)
    expect(notices()).toHaveLength(1)
    expect(notices()[0]).toContain('gave up')
  })
})

describe('headless sessions', () => {
  test('nothing is scheduled or said when no one is at the prompt', async ($, on) => {
    const { clock, compactions } = world(on, 75)
    await $.session.start({ cwd: '/w', surface: null, isInteractive: false })
    const r = await $.tool.call(BG)
    expect(r.context).toBeUndefined()
    await $.turn.complete(TURN)
    await clock.advance(10_000)
    expect(compactions).toEqual([])
  })

  test('an interactive start keeps the mod on', async ($, on) => {
    const { clock, compactions } = world(on, 75)
    await $.session.start({ cwd: '/w', surface: 'terminal', isInteractive: true })
    await $.tool.call(BG)
    await $.turn.complete(TURN)
    await clock.settle()
    expect(compactions).toHaveLength(1)
  })

  test('a task that ends with the final response schedules nothing', async ($, on) => {
    const { clock, compactions } = world(on, 75, ['ok'], 0, { backgroundEndsWithFinalResponse: true })
    const r = await $.tool.call(BG)
    expect(r.context?.join('')).not.toContain('scheduled')
    await $.turn.complete(TURN)
    await clock.advance(10_000)
    expect(compactions).toEqual([])
  })
})

describe('tasks finishing during retries', () => {
  test('a task that finishes between retries skips the compaction', async ($, on) => {
    const { clock, compactions, notices } = world(on, 75, ['reject', 'ok'])
    await $.tool.call(BG)
    await $.turn.complete(TURN)
    await clock.advance(0)
    expect(compactions).toHaveLength(1)
    await finished($, 'b1')
    await clock.advance(10_000)
    expect(compactions).toHaveLength(1)
    expect(notices()).toEqual(['early-compact: skipped (background task already finished)'])
  })
})

describe('context growing with a schedule pending', () => {
  test('the model is nagged once past the margin', async ($, on) => {
    const { usage } = world(on, 75)
    await $.tool.call(BG)
    const r1 = await $.tool.call({ tool: 'Read', file_path: '/w/a' })
    expect(r1.context).toBeUndefined()
    usage.percent = 80
    const r2 = await $.tool.call({ tool: 'Read', file_path: '/w/a' })
    expect(r2.context?.[0]).toContain('already scheduled')
    expect(r2.context?.[0]).toContain('80%')
    usage.percent = 85
    const r3 = await $.tool.call({ tool: 'Read', file_path: '/w/a' })
    expect(r3.context).toBeUndefined()
  })

  test('no nag without a schedule, and a joining task is not nagged', async ($, on) => {
    const { usage } = world(on, 75)
    await $.tool.call({ tool: 'Read', file_path: '/w/a' })
    usage.percent = 85
    const r1 = await $.tool.call({ tool: 'Read', file_path: '/w/a' })
    expect(r1.context).toBeUndefined()
    await $.tool.call(BG)
    const r2 = await $.tool.call(BG)
    expect(r2.context?.[0]).toContain('also waits')
    expect(r2.context?.join('')).not.toContain('already scheduled')
  })
})

describe('guidance above the threshold', () => {
  test('the first tool call above the threshold carries a reminder, once', async ($, on) => {
    const { usage } = world(on, 75)
    const r1 = await $.tool.call({ tool: 'Read', file_path: '/w/a' })
    expect(r1.context?.[0]).toContain('run_in_background: true')
    const r2 = await $.tool.call({ tool: 'Read', file_path: '/w/a' })
    expect(r2.context).toBeUndefined()

    usage.percent = 20
    const r3 = await $.tool.call({ tool: 'Read', file_path: '/w/a' })
    expect(r3.context).toBeUndefined()
    usage.percent = 72
    const r4 = await $.tool.call({ tool: 'Read', file_path: '/w/a' })
    expect(r4.context?.[0]).toContain('72% full')
  })

  test('subagent calls carry no reminder', async ($, on) => {
    world(on, 75)
    const r = await $.tool.call({ tool: 'mcp__other__x', agentId: 'a1' })
    expect(r.context).toBeUndefined()
  })
})

describe('missed chance', () => {
  test('a slow foreground Bash above the threshold toasts and tells the model', async ($, on) => {
    const { toasts } = world(on, 75, ['ok'], 20_000)
    await $.tool.call({ tool: 'Read', file_path: '/w/a' })
    const r = await $.tool.call({ tool: 'Bash', command: 'make' })
    expect(r.context?.[0]).toContain('ran 20s in the foreground')
    expect(toasts).toEqual(['early-compact: missed chance, 20s foreground Bash at 75% context'])
  })

  test('slow background or quick foreground calls do not', async ($, on) => {
    const { toasts } = world(on, 75, ['ok'], 20_000)
    await $.tool.call(BG)
    expect(toasts).toEqual([])
  })

  test('the nudge option turns the toast off, not the guidance', { options: { nudge: false } }, async ($, on) => {
    const { toasts } = world(on, 75, ['ok'], 20_000)
    await $.tool.call({ tool: 'Read', file_path: '/w/a' })
    const r = await $.tool.call({ tool: 'Bash', command: 'make' })
    expect(r.context?.[0]).toContain('in the foreground')
    expect(toasts).toEqual([])
  })
})
