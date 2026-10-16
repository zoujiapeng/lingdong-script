#!/usr/bin/env node
/**
 * Structural verification for the hand-written client bundle.
 *
 * No browser is involved. The script stubs the host module loader, evaluates
 * the real bundle, asserts the export contract, and then drives apply() against
 * a recording fake context to prove every registration actually happens.
 *
 * Usage:  node packages/dsh-client-ui-lingdong/scripts/verify-bundle.mjs
 */
import { readFileSync } from 'node:fs'
import { createContext, runInContext } from 'node:vm'
import { fileURLToPath } from 'node:url'

const BUNDLE = fileURLToPath(new URL('../lib/client.js', import.meta.url))
const failures = []

function check(label, ok, detail) {
  console.log((ok ? 'PASS  ' : 'FAIL  ') + label + (detail ? ' - ' + detail : ''))
  if (!ok) failures.push(label)
}

// ---- stub the host module loader -------------------------------------------
let capture = null
const sandbox = {
  window: { __ModuleLoader__: { load(descriptor) { capture = descriptor } } },
  console,
}
runInContext(readFileSync(BUNDLE, 'utf8'), createContext(sandbox), { filename: BUNDLE })

check('bundle calls window.__ModuleLoader__.load', capture !== null)
check('descriptor id matches package', capture && capture.id === '@lingdong/dsh-client-ui-lingdong', String(capture && capture.id))
check('descriptor exposes a factory', typeof (capture && capture.factory) === 'function')

// ---- stub require ----------------------------------------------------------
const required = []
const element = (type, props) => ({ type, props })
function fakeRequire(name) {
  required.push(name)
  if (name === 'react') return { createElement: element, useMemo: (fn) => fn(), useLayoutEffect: () => {} }
  if (name === 'react/jsx-runtime') return { jsx: element, jsxs: element, Fragment: 'Fragment' }
  throw new Error('unexpected host module: ' + name)
}

const bundle = capture.factory(fakeRequire)
check('exports.apply is a function', typeof bundle.apply === 'function')
check('exports.inject is an array', Array.isArray(bundle.inject), JSON.stringify(bundle.inject))
check(
  'only host-provided modules were required',
  required.every((name) => name === 'react' || name === 'react/jsx-runtime'),
  required.join(','),
)

// ---- drive apply() against a recording context ------------------------------
const effects = []
const seen = { locale: [], tabTypes: [], slotInject: [], slotRegister: [] }
const fakeCtx = {
  effect(fn, label) { effects.push(label); return fn() },
  locale: { register(ns, dict) { seen.locale.push({ ns, langs: Object.keys(dict) }) } },
  sidebarRightTabs: { register(definition) { seen.tabTypes.push(definition) } },
  slots: {
    inject(name, fn) { seen.slotInject.push(name); return fn() },
    register(definition, component) {
      seen.slotRegister.push({ name: definition.name, key: definition.key, kind: typeof component })
    },
  },
}

let threw = null
try { bundle.apply(fakeCtx) } catch (error) { threw = error }
check('apply() does not throw', threw === null, threw ? String(threw.message || threw) : '')
check('apply() registers four effects', effects.length === 4, effects.join(' | '))
check(
  'locale dictionary registered for zh and en',
  seen.locale.length === 1 && seen.locale[0].langs.join(',') === 'zh,en',
  JSON.stringify(seen.locale),
)
check(
  'tab type registered',
  seen.tabTypes.length === 1 && seen.tabTypes[0].kind === 'lingdong-flow',
  JSON.stringify(seen.tabTypes.map((t) => t.kind)),
)
check('canOpen accepts the plugin address', seen.tabTypes[0].canOpen('dsh-resource://lingdong-flow/default') === true)
check('canOpen rejects a foreign address', seen.tabTypes[0].canOpen('dsh-resource://plan/x') === false)
check(
  'two slots injected',
  seen.slotInject.join(',') === 'sidebar.right.pane.tab,sidebar.right.pane.tab.title',
  seen.slotInject.join(','),
)
check('two slots registered', seen.slotRegister.length === 2, JSON.stringify(seen.slotRegister.map((s) => s.name)))
check('both registrations are components', seen.slotRegister.every((s) => s.kind === 'function'))

console.log('')
console.log(failures.length === 0 ? 'ALL CHECKS PASSED' : failures.length + ' CHECK(S) FAILED: ' + failures.join(', '))
process.exitCode = failures.length === 0 ? 0 : 1
