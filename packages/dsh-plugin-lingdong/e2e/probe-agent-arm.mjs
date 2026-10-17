#!/usr/bin/env node
/**
 * Drive the WinPilot runtime's own agent with no DSH in the loop at all.
 *
 * This is the missing arm. The paired E2E compares two computer-use providers,
 * but both of them run inside DSH's agent loop, so neither number says anything
 * about the repository's own LiveVisualActor. This probe submits one goal
 * straight to `/api/v2/goals/runs`, polls the run, and reports what the agent
 * actually spent - the same currency the paired harness reports for the DSH
 * arms (model calls, and prompt/completion tokens per call).
 *
 * Usage:
 *   node packages/dsh-plugin-lingdong/e2e/probe-agent-arm.mjs "<goal>"
 *   node packages/dsh-plugin-lingdong/e2e/probe-agent-arm.mjs --port 8795 "<goal>"
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { resolveConfig } from '../src/config.mjs'
import { WinPilotSidecar } from '../src/sidecar.mjs'
import { createClient } from '../src/client.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO = path.resolve(HERE, '..', '..', '..')
const TRACES = path.join(REPO, 'python', 'winpilot_home', 'traces')

const argv = process.argv.slice(2)
const portIndex = argv.indexOf('--port')
const port = portIndex >= 0 ? Number(argv[portIndex + 1]) : Number(process.env.LD_AGENT_PORT || 8795)
const goalArg = argv.filter((value, index) => index !== portIndex && index !== portIndex + 1).join(' ').trim()
const goal = goalArg || 'Reply with the single word: ok'

/**
 * Read the run's own trace, which is the only place the real turn and token
 * accounting lives: the REST projection reports lifecycle, not cost.
 */
function readTrace(runId) {
  const file = path.join(TRACES, `${runId}.jsonl`)
  if (!existsSyncSafe(file)) return null
  const lines = readFileSync(file, 'utf8').split('\n').filter((line) => line.startsWith('{'))
  const prompts = []
  let completion = 0
  let actions = 0
  let rejected = 0
  const proposals = []
  let done = null
  let admission = null
  for (const line of lines) {
    let event
    try { event = JSON.parse(line) } catch { continue }
    if (event.type === 'arp_run_start') admission = event.goal_admission ?? null
    if (event.type === 'agent_usage' && event.step_usage) {
      prompts.push(event.step_usage.prompt_tokens ?? 0)
      completion += event.step_usage.completion_tokens ?? 0
    }
    if (event.type === 'vnext_physical_attempt') actions += 1
    if (event.type === 'vnext_raw_receipt' && event.error_kind) rejected += 1
    if (event.type === 'vnext_model_tool_shape' && Array.isArray(event.calls)) {
      for (const call of event.calls) proposals.push(call.name)
    }
    if (event.type === 'live_agent_done') done = event
  }
  return {
    file: path.basename(file),
    admission,
    model_calls: prompts.length,
    prompts,
    prompt_tokens: prompts.reduce((sum, value) => sum + value, 0),
    completion_tokens: completion,
    physical_attempts: actions,
    rejected_receipts: rejected,
    proposals,
    status: done?.status ?? '',
    failure_stage: done?.failure_stage ?? '',
    turns_used: done?.turns_used ?? 0,
    detail: done?.detail ?? '',
  }
}

function existsSyncSafe(file) {
  try { statSync(file); return true } catch { return false }
}

function newestTrace() {
  const files = readdirSync(TRACES)
    .filter((name) => name.endsWith('.jsonl'))
    .map((name) => ({ name, at: statSync(path.join(TRACES, name)).mtimeMs }))
    .sort((left, right) => right.at - left.at)
  return files[0]?.name ?? ''
}

const settings = resolveConfig({ port, autoStart: true })
const sidecar = new WinPilotSidecar(settings, {
  info: (message) => process.stderr.write(`${message}\n`),
  warn: (message) => process.stderr.write(`${message}\n`),
})
const client = createClient(settings, sidecar)

console.log(`runtime  : ${sidecar.baseUrl}`)
await sidecar.ensure()

let status = null
try { status = await client.get('/api/status') } catch (error) { status = { error: String(error.message) } }
// The flag is nested under `vnext`, next to the frozen turn/model/time budget.
const liveEnabled = status?.vnext?.live_agent_enabled
console.log(`reachable: ${status && !status.error}   live_agent_enabled: ${liveEnabled}`)
console.log(`model    : ${status?.model_profile?.provider}/${status?.model_profile?.model}  vision=${status?.model_profile?.supports_vision}`)
if (liveEnabled !== true) {
  console.error('the runtime reports the live agent disabled; refusing to submit')
  await sidecar.stop()
  process.exit(2)
}

console.log(`goal     : ${goal}`)
const submitted = await client.post('/api/v2/goals/runs', { goal })
const runId = submitted.run_id ?? submitted.run?.run_id
if (!runId) throw new Error(`runtime accepted the goal but reported no run id: ${JSON.stringify(submitted).slice(0, 300)}`)
console.log(`run_id   : ${runId}`)

const started = Date.now()
const final = await client.waitForRun(runId, { timeoutMs: Number(process.env.LD_AGENT_TIMEOUT_MS || 600_000) })
const elapsed = Math.round((Date.now() - started) / 1000)

const terminal = client.statusOf(final)
const reply = final?.run?.result ?? final?.run?.reply ?? null
console.log(`status   : ${terminal}   (phase=${final?.run?.phase ?? '?'}, timed_out=${Boolean(final?.timed_out)}, ${elapsed}s)`)
if (reply) console.log(`result   : ${String(reply).slice(0, 400)}`)

const trace = readTrace(runId)
if (!trace) {
  console.log(`trace    : not found at traces/${runId}.jsonl (newest is ${newestTrace()})`)
} else {
  console.log(`trace    : ${trace.file}`)
  console.log(`agent    : status=${trace.status} failure_stage=${trace.failure_stage || '-'} turns_used=${trace.turns_used}`)
  console.log(`cost     : model_calls=${trace.model_calls} prompt_tokens=${trace.prompt_tokens} completion_tokens=${trace.completion_tokens}`)
  console.log(`prompts  : ${trace.prompts.join(', ')}`)
  console.log(`physical : attempts=${trace.physical_attempts} rejected_receipts=${trace.rejected_receipts}`)
  console.log(`proposed : ${trace.proposals.join(', ')}`)
}

await sidecar.stop()
process.exit(terminal && !final?.timed_out ? 0 : 1)
