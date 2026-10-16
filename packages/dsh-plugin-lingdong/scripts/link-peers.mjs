#!/usr/bin/env node
/**
 * Link this plugin's runtime peer dependencies for a DSH profile install.
 *
 * WHY THIS EXISTS
 * `dsh plugin add <path>` installs a local package as a Windows junction, so the
 * module's real path stays inside this repository. Node then resolves bare
 * specifiers from the repository, where nothing ships @deepseek-ai/*. DSH's own
 * local plugins avoid this by using only relative imports; this plugin needs
 * three real ones, so it carries its own node_modules of junctions pointing at
 * the copies the profile already installed.
 *
 * The generated node_modules/ is git-ignored and rebuilt by this script; it
 * never modifies the DSH installation or the profile's own files.
 *
 * Usage:  node packages/dsh-plugin-lingdong/scripts/link-peers.mjs
 */
import { existsSync, mkdirSync, rmSync, symlinkSync } from 'node:fs'
import { homedir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const PACKAGE_ROOT = path.resolve(fileURLToPath(new URL('../', import.meta.url)))
const DSH_HOME = process.env.DSH_HOME || path.join(homedir(), '.dsh')
const PROFILE = process.env.DSH_PROFILE || 'web'

/** Direct runtime imports; every transitive peer resolves from the real package location. */
const REQUIRED = [
  '@deepseek-ai/schemastery',
  '@deepseek-ai/dsh-computer-use',
  '@deepseek-ai/dsh-mcp-client',
]

/** Search order: the profile's own node_modules first, then the shared parent. */
function searchRoots() {
  return [
    path.join(DSH_HOME, 'profiles', PROFILE, 'node_modules'),
    path.join(DSH_HOME, 'profiles', 'node_modules'),
  ]
}

function resolvePackage(spec) {
  for (const root of searchRoots()) {
    const candidate = path.join(root, ...spec.split('/'))
    if (existsSync(path.join(candidate, 'package.json'))) return candidate
  }
  return null
}

const target = path.join(PACKAGE_ROOT, 'node_modules')
mkdirSync(target, { recursive: true })

let linked = 0
const missing = []
for (const spec of REQUIRED) {
  const source = resolvePackage(spec)
  if (!source) { missing.push(spec); continue }
  const link = path.join(target, ...spec.split('/'))
  mkdirSync(path.dirname(link), { recursive: true })
  rmSync(link, { recursive: true, force: true })
  symlinkSync(source, link, 'junction')
  console.log(`linked ${spec} -> ${source}`)
  linked += 1
}

if (missing.length) {
  console.error(`missing from the DSH profile: ${missing.join(', ')}`)
  console.error(`install them first:  dsh plugin --profile ${PROFILE} add <package>`)
  process.exitCode = 1
} else {
  console.log(`${linked} peer link(s) ready under ${target}`)
}
