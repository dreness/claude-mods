import type { EngineInterface, Register, ToolCallInput } from 'claude-code'

const DEFAULT_THRESHOLD = 70
const SLOW_MS = 15_000
const RETRY_MS = 250
const MAX_RETRIES = 8
// Turn ends a rejected compaction waits through before it is dropped.
const MAX_REARMS = 1
// Context growth past the threshold, in points, after which a pending schedule nags the model once.
const NAG_MARGIN = 10
const TASK_ID = /<task-id>([^<]+)<\/task-id>/g

const reminder = (percent: number) =>
  `Context is ${percent}% full, near auto-compaction. Until it is compacted, run every shell command that may take ${SLOW_MS / 1000}s or more (tests, builds, installs, linters, long scripts) with run_in_background: true, including ones you would normally run in the foreground. If you are then only waiting on it, end your turn: early-compact compacts the conversation while it runs, and its completion notice resumes you.`

const scheduled = (id: string, percent: number) =>
  `early-compact: compaction scheduled while background task ${id} runs (context ${percent}% full). If you are only waiting on it, end your turn now with a one-line note; do not poll or sleep. Its completion notice resumes you after compaction.`

const alsoWaits = (id: string) => `early-compact: the scheduled compaction also waits on background task ${id}.`

const nag = (percent: number) =>
  `early-compact: a compaction is already scheduled for the end of this turn and context has grown to ${percent}%, close to auto-compaction. If you are only waiting on background tasks, end your turn now.`

const missed = (seconds: number) =>
  `That command ran ${seconds}s in the foreground with context above the compaction threshold. Run commands like it with run_in_background: true, then end your turn while it runs.`

const instructionsFor = (tasks: Record<string, string>) =>
  `Background tasks still running: ${Object.entries(tasks)
    .map(([id, label]) => `${id} (${label})`)
    .join('; ')}. Their completion notices arrive after this compaction: read each one's output, then carry on with the work in progress.`

// tasks: background task id -> what it runs, for those not yet reported finished.
// epoch: State.compactions when scheduled; a compaction since then makes this one redundant.
// nagged: the model was told once that context kept growing with this schedule pending.
type Pending = { tasks: Record<string, string>; rearms: number; epoch: number; nagged?: boolean }
// reminded: the model was told to background slow commands since context last crossed the threshold.
// compactions: main-conversation compactions seen, from any trigger.
// active: the job whose compaction is being attempted; its tasks keep being tracked until it resolves.
// interactive: false under -p or the SDK, where compact() is unavailable between turns.
type State = { pending?: Pending; active?: Pending; reminded?: boolean; compactions: number; interactive: boolean }

export const register: Register = (on, options) => {
  const threshold = typeof options.threshold === 'number' ? options.threshold : DEFAULT_THRESHOLD
  const nudge = options.nudge !== false
  const state: State = { compactions: 0, interactive: true }

  on('session.start', ($, e, next) => {
    state.interactive = e.isInteractive
    return next(e)
  })

  // Auto-compact can land between scheduling and turn end; ours would then compact a fresh summary.
  on('session.compact', async ($, e, next) => {
    const r = await next(e)
    if (e.agentId === undefined && e.trigger !== 'precompute' && r.skip === undefined) state.compactions++
    return r
  })

  // A task's notification row, queued or delivered, means it no longer needs waiting on.
  on('session.append', ($, e, next) => {
    if (e.agentId === undefined) {
      for (const block of e.message.content) {
        if (block.type !== 'text' || typeof block.text !== 'string') continue
        for (const [, id] of block.text.matchAll(TASK_ID)) {
          if (id === undefined) continue
          if (state.pending !== undefined) delete state.pending.tasks[id]
          if (state.active !== undefined) delete state.active.tasks[id]
        }
      }
    }
    return next(e)
  })

  on('turn.complete', ($, e, next) => {
    const job = state.pending
    if (e.agentId !== undefined || job === undefined) return next(e)
    state.pending = undefined
    if (job.epoch !== state.compactions) notice($, 'early-compact: skipped (already compacted)')
    // Aborted or failed turns mean the person is back; leave the conversation alone.
    else if (e.reason !== 'answer') notice($, `early-compact: dropped (turn ended: ${e.reason})`)
    // Nothing left to overlap; auto-compact frees more context later at the same cost.
    else if (Object.keys(job.tasks).length === 0) notice($, 'early-compact: skipped (background task already finished)')
    else compactWhenIdle($, state, job, 0)
    return next(e)
  })

  on('tool.call', async ($, e, next) => {
    if (e.agentId !== undefined) return next(e)
    const startedAt = await $.clock.now().catch(() => undefined)
    const r = await next(e)
    if (r.deny !== undefined) return r
    // Guarded here, not with .catch, which would run the tool again.
    try {
      // A task ended with the final response sends no completion notice, so nothing would resume the model.
      const res = r.result as { backgroundTaskId?: string; backgroundEndsWithFinalResponse?: true }
      const taskId =
        e.tool === 'Bash' && !r.isError && res.backgroundEndsWithFinalResponse !== true ? res.backgroundTaskId : undefined
      const notes = await guidance($, e, taskId, startedAt, state, { threshold, nudge })
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
  taskId: string | undefined,
  startedAt: number | undefined,
  state: State,
  { threshold, nudge }: Config,
): Promise<string[]> {
  if (!state.interactive) return []
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
  const pending = state.pending?.epoch === state.compactions ? state.pending : undefined
  // The turn keeps going with a schedule pending; auto-compact wins if it reaches its limit first.
  if (pending !== undefined && !pending.nagged && taskId === undefined && percent >= threshold + NAG_MARGIN) {
    pending.nagged = true
    notes.push(nag(percent))
  }
  if (taskId !== undefined && e.tool === 'Bash') {
    const label = (e.description ?? e.command).slice(0, 200)
    const job = state.pending
    if (job === undefined || job.epoch !== state.compactions) {
      state.pending = { tasks: { [taskId]: label }, rearms: 0, epoch: state.compactions }
      notes.push(scheduled(taskId, percent))
    } else {
      job.tasks[taskId] = label
      notes.push(alsoWaits(taskId))
    }
  } else if (e.tool === 'Bash' && startedAt !== undefined) {
    const elapsed = (await $.clock.now()) - startedAt
    const seconds = Math.round(elapsed / 1000)
    if (elapsed >= SLOW_MS) {
      notes.push(missed(seconds))
      if (nudge) $.ui.toast(`early-compact: missed chance, ${seconds}s foreground Bash at ${percent}% context`)
    }
  }
  return notes
}

// A system row: shown in the transcript, never read by the model.
function notice($: EngineInterface, text: string): void {
  $.session.append({ message: { type: 'system', content: [{ type: 'text', text }] } }).catch(() => $.ui.toast(text))
}

// compact() rejects while a turn runs, e.g. one a queued notification started.
// Retry briefly, then wait for that turn to end.
function compactWhenIdle($: EngineInterface, state: State, job: Pending, attempt: number): void {
  state.active = job
  const done = (text?: string) => {
    state.active = undefined
    $.ui.status(undefined)
    if (text !== undefined) notice($, text)
  }
  $.clock.after(attempt === 0 ? 0 : RETRY_MS, () => {
    if (job.epoch !== state.compactions) return done()
    // Tasks can finish during the retries or the rearm turn.
    if (Object.keys(job.tasks).length === 0) return done('early-compact: skipped (background task already finished)')
    $.ui.status('early-compact: compacting while waiting...')
    $.session
      .compact({ instructions: instructionsFor(job.tasks) })
      .then(r => done(r.skip !== undefined ? `early-compact: skipped (${r.skip})` : 'early-compact: compacted while waiting'))
      .catch((err: unknown) => {
        $.ui.status(undefined)
        if (attempt < MAX_RETRIES) return compactWhenIdle($, state, job, attempt + 1)
        if (job.rearms < MAX_REARMS && state.pending === undefined) {
          state.pending = { ...job, rearms: job.rearms + 1 }
          return done()
        }
        done(`early-compact: gave up: ${String(err).slice(0, 200)}`)
      })
  })
}
