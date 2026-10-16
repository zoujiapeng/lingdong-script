#!/usr/bin/env node
/**
 * Structural and render verification for the hand-written client bundle.
 *
 * No browser is involved, but this is stronger than a shape check: the bundle
 * is evaluated against a stubbed host module loader with the REAL react and
 * react/jsx-runtime, then apply() is driven against a recording fake context
 * and the registered pane component is server-rendered. A component that
 * cannot render fails here.
 *
 * Usage:  node packages/dsh-client-ui-lingdong/scripts/verify-bundle.mjs
 */
import { readFileSync } from 'node:fs'
import { createContext, runInContext } from 'node:vm'
import { fileURLToPath } from 'node:url'
import * as React from 'react'
import * as jsxRuntime from 'react/jsx-runtime'
import { renderToStaticMarkup } from 'react-dom/server'

const BUNDLE = fileURLToPath(new URL('../lib/client.js', import.meta.url))
const failures = []

function check(label, ok, detail) {
  console.log((ok ? 'PASS  ' : 'FAIL  ') + label + (detail ? ' - ' + detail : ''))
  if (!ok) failures.push(label)
}

function count(haystack, needle) {
  let total = 0
  let at = haystack.indexOf(needle)
  while (at !== -1) {
    total += 1
    at = haystack.indexOf(needle, at + needle.length)
  }
  return total
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

// ---- real react through the stubbed require --------------------------------
const required = []
function fakeRequire(name) {
  required.push(name)
  if (name === 'react') return React
  if (name === 'react/jsx-runtime') return jsxRuntime
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
      seen.slotRegister.push({ name: definition.name, key: definition.key, kind: typeof component, component })
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

// ---- render the registered pane with the real React ------------------------
const paneEntry = seen.slotRegister.find((entry) => entry.name === 'sidebar.right.pane.tab')
let markup = ''
let renderError = null
try {
  markup = renderToStaticMarkup(React.createElement(paneEntry.component, {}))
} catch (error) {
  renderError = error
}
check('pane component server-renders', renderError === null, renderError ? String(renderError.message || renderError) : String(markup.length) + ' chars')
check('workbench root rendered', markup.indexOf('data-lingdong-flow-workbench') !== -1)
check('edge layer rendered as svg', markup.indexOf('<svg') !== -1 && count(markup, '<path') === 4, count(markup, '<path') + ' paths')
check('initial nodes rendered', count(markup, 'data-lingdong-node=') === 4, String(count(markup, 'data-lingdong-node=')))
check('palette offers the offline node vocabulary', count(markup, 'data-lingdong-palette=') === 8, String(count(markup, 'data-lingdong-palette=')))
check('recorded fragment node is in the palette', markup.indexOf('data-lingdong-palette="replay_step"') !== -1)
check('agent-semantic nodes are absent', markup.indexOf('data-lingdong-palette="agent_goal"') === -1 && markup.indexOf('data-lingdong-palette="skill_replay"') === -1)
check('canvas surface rendered', markup.indexOf('data-lingdong-canvas') !== -1)
check('status bar reports zero model calls', markup.indexOf('0 \u6a21\u578b\u8c03\u7528') !== -1, 'expected the model-call counter in the status bar')
check('inspector starts empty', markup.indexOf('\u9009\u4e2d\u4e00\u4e2a\u8282\u70b9') !== -1)

console.log('')
console.log(failures.length === 0 ? 'ALL CHECKS PASSED' : failures.length + ' CHECK(S) FAILED: ' + failures.join(', '))
process.exitCode = failures.length === 0 ? 0 : 1
