#!/usr/bin/env node
/**
 * Show how each arm actually spent its steps on one task.
 *
 * The paired report records tool calls and step counts but not *what* the steps
 * were for, and the diff between arms is the whole question: "this arm used more
 * steps" is not a finding until you can see whether the extra steps were the
 * tool being expensive or the agent choosing to verify more.
 *
 * Usage:
 *   node packages/dsh-plugin-lingdong/e2e/explain.mjs --latest read_target_pixels
 *   node packages/dsh-plugin-lingdong/e2e/explain.mjs output/e2e/report-123.json read_target_pixels
 *   node packages/dsh-plugin-lingdong/e2e/explain.mjs --latest --all
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPORTS = path.resolve(HERE, '..', '..', '..', 'output', 'e2e')

function latestReport() {
  const files = readdirSync(REPORTS)
    .filter((name) => name.startsWith('report-') && name.endsWith('.json'))
    .map((name) => ({ name, at: statSync(path.join(REPORTS, name)).mtimeMs }))
    .sort((left, right) => right.at - left.at)
  if (!files.length) throw new Error(`no reports in ${REPORTS}`)
  return path.join(REPORTS, files[0].name)
}

/**
 * One tool call, as recorded in the run transcript.
 *
 * Note the bound: the harness keeps only the first few thousand characters of a
 * transcript, so a long run shows its opening calls and not its whole shape.
 * That is enough to see how a run started, not enough to account for every step.
 */
function callsOf(row) {
  const calls = []
  for (const line of String(row.transcript ?? '').split('\n')) {
    const trimmed = line.trim()
    if (!trimmed.startsWith('{')) continue
    let event = null
    try { event = JSON.parse(trimmed) } catch { continue }
    if (event?.type === 'tool_call') {
      calls.push({ tool: String(event.tool ?? event.name ?? '?'), input: event.input ?? event.arguments ?? {} })
    } else if (event?.type === 'tool_result' && calls.length) {
      calls[calls.length - 1].result = JSON.stringify(event.result ?? event.content ?? '').slice(0, 120)
    }
  }
  return calls
}

const argv = process.argv.slice(2)
const positional = argv.filter((value) => !value.startsWith('--'))
const explicitReport = positional.find((value) => value.endsWith('.json'))
const taskId = positional.find((value) => !value.endsWith('.json'))
const showAll = argv.includes('--all')
const report = explicitReport ?? latestReport()

const rows = JSON.parse(readFileSync(report, 'utf8'))
console.log(`report: ${path.basename(report)}`)
// One named task, or every task in the report.
const tasks = taskId ? [taskId] : [...new Set(rows.map((row) => row.task))]

for (const id of tasks) {
  for (const row of rows.filter((entry) => entry.task === id)) {
    console.log(`\n=== ${id} / ${row.arm}: ${row.steps} steps, ${row.total_tokens} tokens, pass=${row.pass} (${row.detail})`)
    if (!showAll) continue
    const calls = callsOf(row)
    for (const [index, call] of calls.entries()) {
      console.log(`  ${String(index + 1).padStart(2)}. ${call.tool} ${JSON.stringify(call.input).slice(0, 150)}`)
      if (call.result) console.log(`      -> ${call.result}`)
    }
    if (!calls.length) console.log('  (no tool_calls recorded in the transcript)')
  }
}
