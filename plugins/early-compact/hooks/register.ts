import type { EngineInterface, Register } from 'claude-code'

const NAME = 'compact_while_waiting'
const DEFAULT_THRESHOLD = 60
const SLOW_MS = 30_000
const RETRY_MS = 250
const MAX_RETRIES = 8
// Turn ends a rejected compaction waits through before it is dropped.
const MAX_REARMS = 1

const DESCRIPTION = `Compacts the conversation while a backgrounded command runs, so compaction happens during idle time instead of mid-task later.

Use it when you are about to run a shell command you expect to take ${SLOW_MS / 1000}s or more (build, full test suite, install, long script):
1. Run the command with run_in_background: true.
2. Call this tool, passing in \`instructions\` the background task id, what the command does, and what you will do with its output.
3. If the tool says compaction is scheduled, end your turn immediately with a one-line note. Do not poll or sleep; the task's completion notification will resume you after compaction.

If it says compaction is not needed, carry on as usual.`

type Pending = { instructions: string; rearms: number }
type State = { pending?: Pending }

export const register: Register = (on, options) => {
  const threshold = typeof options.threshold === 'number' ? options.threshold : DEFAULT_THRESHOLD
  const nudge = options.nudge !== false
  const state: State = {}

  on('session.start', async ($, e, next) => {
    await $.tool.register({
      name: NAME,
      description: DESCRIPTION,
      inputSchema: {
        type: 'object',
        properties: {
          instructions: {
            type: 'string',
            description: 'What the summary must keep: the background task id, its purpose, next steps.',
          },
        },
        required: ['instructions'],
      },
      isDeferred: false,
    })
    return next(e)
  })

  on('tool.call', { tool: `mcp__early-compact__${NAME}` }, async ($, e) => {
    if (e.agentId !== undefined) return { result: 'Not available in subagents; carry on.' }
    const { context } = await $.session.usage()
    const percent = context.percent ?? 0
    if (percent < threshold) {
      return { result: `Context is ${percent}% full (threshold ${threshold}%); compaction not needed. Carry on.` }
    }
    state.pending = { instructions: String((e as { instructions?: unknown }).instructions ?? ''), rearms: 0 }
    return {
      result: `Context is ${percent}% full. Compaction is scheduled for when this turn ends. End your turn now; do not poll the background task.`,
    }
  }).catch(() => ({ result: 'early-compact failed; carry on without it.' }))

  on('turn.complete', ($, e, next) => {
    const job = state.pending
    if (e.agentId !== undefined || job === undefined) return next(e)
    state.pending = undefined
    // Aborted or failed turns mean the person is back; leave the conversation alone.
    if (e.reason === 'answer') compactWhenIdle($, state, job, 0)
    return next(e)
  })

  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    if (nudge && e.agentId === undefined && !e.run_in_background && (e.timeout ?? 0) >= SLOW_MS) {
      const { context } = await $.session.usage()
      if ((context.percent ?? 0) >= threshold) {
        $.ui.toast(`early-compact: missed chance, foreground Bash at ${context.percent}% context`)
      }
    }
    return next(e)
  }).catch(($, e, next) => next(e))
}

// compact() rejects while a turn runs, e.g. one a queued notification started.
// Retry briefly, then wait for that turn to end.
function compactWhenIdle($: EngineInterface, state: State, job: Pending, attempt: number): void {
  $.clock.after(attempt === 0 ? 0 : RETRY_MS, () => {
    $.ui.status('early-compact: compacting while waiting...')
    $.session
      .compact({ instructions: job.instructions })
      .then(r => {
        $.ui.status(undefined)
        $.ui.toast(r.skip !== undefined ? `early-compact: skipped (${r.skip})` : 'early-compact: compacted while waiting')
      })
      .catch((err: unknown) => {
        $.ui.status(undefined)
        if (attempt < MAX_RETRIES) return compactWhenIdle($, state, job, attempt + 1)
        if (job.rearms < MAX_REARMS && state.pending === undefined) {
          state.pending = { ...job, rearms: job.rearms + 1 }
          return
        }
        $.ui.toast(`early-compact: gave up: ${String(err).slice(0, 200)}`)
      })
  })
}
