import type { EngineInterface, Register, ToolCallInput } from 'claude-code'

const NAME = 'compact_while_waiting'
const DEFAULT_THRESHOLD = 60
const SLOW_MS = 15_000
const RETRY_MS = 250
const MAX_RETRIES = 8
// Turn ends a rejected compaction waits through before it is dropped.
const MAX_REARMS = 1

const DESCRIPTION = `Compacts the conversation while a backgrounded command runs, so compaction happens during idle time instead of mid-task later.

Use it when you are about to run a shell command that may take ${SLOW_MS / 1000}s or more (test suite, build, install, linter, long script):
1. Run the command with run_in_background: true, even one you would otherwise run in the foreground.
2. Call this tool, passing in \`instructions\` the background task id, what the command does, and what you will do with its output.
3. If the tool says compaction is scheduled, end your turn immediately with a one-line note. Do not poll or sleep; the task's completion notification will resume you after compaction.

If it says compaction is not needed, carry on as usual.`

const reminder = (percent: number) =>
  `Context is ${percent}% full, near auto-compaction. Until it is compacted, run every shell command that may take ${SLOW_MS / 1000}s or more (tests, builds, installs, linters, long scripts) with run_in_background: true, including ones you would normally run in the foreground, then call ${NAME} as its description says. Waiting on that command then pays for the compaction.`

const missed = (seconds: number) =>
  `That command ran ${seconds}s in the foreground with context above the compaction threshold. Run commands like it with run_in_background: true and call ${NAME}.`

type Pending = { instructions: string; rearms: number }
// reminded: the model was told to background slow commands since context last crossed the threshold.
type State = { pending?: Pending; reminded?: boolean }

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

  on('tool.call', async ($, e, next) => {
    if (e.agentId !== undefined || e.tool === `mcp__early-compact__${NAME}`) return next(e)
    const startedAt = await $.clock.now().catch(() => undefined)
    const r = await next(e)
    if (r.deny !== undefined) return r
    // Guarded here, not with .catch, which would run the tool again.
    try {
      const notes = await guidance($, e, startedAt, state, { threshold, nudge })
      return notes.length === 0 ? r : { ...r, context: [...(r.context ?? []), notes.join('\n\n')] }
    } catch {
      return r
    }
  })
}

type Config = { threshold: number; nudge: boolean }

async function guidance(
  $: EngineInterface,
  e: ToolCallInput,
  startedAt: number | undefined,
  state: State,
  { threshold, nudge }: Config,
): Promise<string[]> {
  const { context } = await $.session.usage()
  const percent = context.percent ?? 0
  if (percent < threshold) {
    state.reminded = false
    return []
  }
  const notes: string[] = []
  if (!state.reminded) {
    state.reminded = true
    notes.push(reminder(percent))
  }
  if (e.tool === 'Bash' && !e.run_in_background && startedAt !== undefined) {
    const elapsed = (await $.clock.now()) - startedAt
    const seconds = Math.round(elapsed / 1000)
    if (elapsed >= SLOW_MS) {
      notes.push(missed(seconds))
      if (nudge) $.ui.toast(`early-compact: missed chance, ${seconds}s foreground Bash at ${percent}% context`)
    }
  }
  return notes
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
