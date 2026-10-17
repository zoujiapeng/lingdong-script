#!/usr/bin/env node
/**
 * Read several comparison rounds at once.
 *
 * One round of three arms is a sample, not a rate. The question this answers is
 * "does this arm do the same thing twice?" - which a single report can never
 * answer, and which is exactly where the agent arm is expected to be weakest
 * (its two failures were a loop and a geometry miss, both of which are one bad
 * decision away from passing).
 *
 * Usage:
 *   node packages/dsh-plugin-lingdong/e2e/compare.mjs                 # newest two rounds
 *   node packages/dsh-plugin-lingdong/e2e/compare.mjs --rounds 3      # newest three
 *   node packages/dsh-plugin-lingdong/e2e/compare.mjs a.json b.json   # explicit
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPORTS = path.resolve(HERE, '..', '..', '..', 'output', 'e2e')

/** Reports ordered oldest to newest, so a round index means the same thing everywhere. */
function newestReports(count) {
  return readdirSync(REPORTS)
    .filter((name) => name.startsWith('report-') && name.endsWith('.json'))
    .map((name) => ({ name, at: statSync(path.join(REPORTS, name)).mtimeMs }))
    .sort((left, right) => right.at - left.at)
    .slice(0, count)
    .reverse()
    .map((entry) => path.join(REPORTS, entry.name))
}

const argv = process.argv.slice(2)
const positional = argv.filter((value) => !value.startsWith('--') && path.extname(value) === '.json')
const rounds = argv.includes('--rounds') ? Number(argv[argv.indexOf('--rounds') + 1]) : 2
const files = positional.length ? positional : newestReports(rounds)

// Only rounds that actually ran a given arm count as rounds for that arm.
const perArm = new Map()
for (const [index, file] of files.entries()) {
  const rows = JSON.parse(readFileSync(file, 'utf8'))
  for (const row of rows) {
    const arm = perArm.get(row.arm) ?? { rounds: new Map(), tasks: new Map() }
    perArm.set(row.arm, arm)
    arm.rounds.set(index, file)
    const task = arm.tasks.get(row.task) ?? []
    task.push({
      round: index,
      pass: Boolean(row.pass),
      steps: row.steps ?? 0,
      tokens: row.total_tokens ?? 0,
      detail: row.detail ?? '',
    })
    arm.tasks.set(row.task, task)
  }
}

console.log(`rounds (oldest first): ${files.map((file) => path.basename(file)).join('  ')}\n`)

for (const [arm, data] of [...perArm.entries()]) {
  const taskNames = [...data.tasks.keys()]
  const passes = taskNames.reduce((sum, name) => sum + data.tasks.get(name).filter((r) => r.pass).length, 0)
  const runs = taskNames.reduce((sum, name) => sum + data.tasks.get(name).length, 0)
  console.log(`=== ${arm}: ${passes}/${runs} across ${data.rounds.size} round(s) ===`)
  for (const name of taskNames) {
    const rows = data.tasks.get(name)
    const cells = rows.map((r) => {
      const tokens = r.tokens >= 1000 ? `${Math.round(r.tokens / 1000)}k` : String(r.tokens)
      return `${r.pass ? 'PASS' : 'FAIL'} ${String(r.steps).padStart(3)}st ${tokens.padStart(6)}`
    }).join('  |  ')
    const verdicts = rows.map((r) => r.pass)
    const unstable = verdicts.length > 1 && new Set(verdicts).size > 1
    console.log(`  ${name.padEnd(21)} ${cells}${unstable ? '   <-- verdict changed between rounds' : ''}`)
  }
  console.log('')
}
