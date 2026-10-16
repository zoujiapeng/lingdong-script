#!/usr/bin/env node
/**
 * Structural, codec and render verification for the hand-written client bundle.
 *
 * No browser is involved, but this is stronger than a shape check:
 *
 * 1. the bundle is evaluated against a stubbed host module loader with the REAL
 *    react and react/jsx-runtime, and apply() is driven against a recording fake
 *    context;
 * 2. the exported canvas codec is exercised directly - a recorded
 *    `.automation.json` is exploded onto nodes and compiled back, and the result
 *    must equal the original apart from the two defaults the canvas normalizes;
 * 3. the registered pane component is server-rendered, so a component that
 *    cannot render fails here.
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

/** Stable stringify so two documents compare by value, not key order. */
function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value && typeof value === 'object') {
    return '{' + Object.keys(value).sort().map((key) => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}'
  }
  return JSON.stringify(value === undefined ? null : value)
}

/**
 * Drop the defaults the canvas deliberately normalizes, so the round-trip
 * comparison states exactly what is promised: field for field, apart from an
 * empty `else` block and `repeat: 1`, which are the replayer's own defaults.
 */
function normalized(steps) {
  return steps.map((step) => {
    const copy = JSON.parse(JSON.stringify(step))
    if (Array.isArray(copy.else) && copy.else.length === 0) delete copy.else
    if (copy.repeat === 1) delete copy.repeat
    if (Array.isArray(copy.then)) copy.then = normalized(copy.then)
    return copy
  })
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
check('the bundle exports the canvas codec', Boolean(bundle.flowCodec) && typeof bundle.flowCodec.toDocument === 'function')

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

// ---- the canvas codec ------------------------------------------------------
const codec = bundle.flowCodec

check(
  'the palette is the deterministic vocabulary only',
  codec.PALETTE.map((entry) => entry.kind).join(',') === 'click,input_text,keyboard,wait,condition,screenshot,replay_step,run_command',
  codec.PALETTE.map((entry) => entry.kind).join(','),
)

// One node kind compiles to exactly one step, in the recorded shape.
const clickNodes = codec.layoutNodes([{ kind: 'click', params: { x: 420, y: 300, button: 'left', double: false, window: 'Notepad' } }])
const clickCompiled = codec.compileNodes(clickNodes)
check(
  'a click node compiles to the recorded click shape plus its target window',
  canonical(clickCompiled.steps) === canonical([{ tool: 'click', coords: [420, 300], target_window: { title: 'Notepad' } }]),
  canonical(clickCompiled.steps),
)
check(
  'the compile map points step 1 at the click node',
  clickCompiled.owners['1']?.nodeId === clickNodes[1].id && clickCompiled.owners['1']?.kind === 'step',
  canonical(clickCompiled.owners),
)

const mixed = codec.layoutNodes([
  { kind: 'wait', params: { seconds: 0.5 } },
  { kind: 'keyboard', params: { keys: 'ctrl+s', repeat: 1 } },
  { kind: 'input_text', params: { text: 'hello', window: '' } },
  { kind: 'run_command', params: { command: 'Get-Date', shell: 'powershell', timeout_s: 5 } },
  { kind: 'screenshot', params: { path: 'shots/a.png' } },
])
const mixedCompiled = codec.compileNodes(mixed)
check(
  'every editable kind compiles to its exact step',
  canonical(mixedCompiled.steps) === canonical([
    { tool: 'ext.delay', seconds: 0.5 },
    { tool: 'hotkey', keys: ['ctrl', 's'] },
    { tool: 'type_text', text_input: 'hello' },
    { tool: 'run_shell', command: 'Get-Date', shell: 'powershell', timeout_s: 5 },
    { tool: 'ext.screenshot', path: 'shots/a.png' },
  ]),
  canonical(mixedCompiled.steps),
)

// A condition owns the rest of the pipeline: that is the only linear shape the
// replayer reproduces exactly.
const condNodes = codec.layoutNodes([
  { kind: 'condition', params: { key: 'file_exists', value: 'C:/tmp/a.txt', timeout_s: 6 } },
  { kind: 'wait', params: { seconds: 2 } },
])
const condCompiled = codec.compileNodes(condNodes)
check(
  'a condition compiles to one ctrl.if wrapping the rest of the pipeline',
  canonical(condCompiled.steps) === canonical([
    { tool: 'ctrl.if', cond: { file_exists: 'C:/tmp/a.txt' }, then: [{ tool: 'ext.delay', seconds: 2 }], timeout_s: 6 },
  ]),
  canonical(condCompiled.steps),
)
check(
  'the nested step keeps the replayer index path the condition creates',
  condCompiled.owners['1']?.kind === 'ctrl'
    && condCompiled.owners['1.1']?.nodeId === condNodes[2].id,
  canonical(condCompiled.owners),
)

let blocked = null
try { codec.compileNodes(codec.layoutNodes([{ kind: 'keyboard', params: { keys: '' } }])) } catch (error) { blocked = error }
check('an uncompilable node is refused instead of silently dropped', blocked !== null, blocked ? String(blocked.message) : 'no error')

// A recorded document round-trips through the canvas unchanged.
const recorded = {
  format: 'automation-ir/1',
  id: 'air-verify',
  name: 'recorded demo',
  created: 1_700_000_000,
  source: { kind: 'wps', format: 'wps/2', source: 'winpilot', source_run: 'arp-1', task: 'demo' },
  conditions: {},
  evidence: { required: [] },
  steps: [
    { tool: 'focus_window', title: 'Notepad' },
    { tool: 'click', coords: [420, 300], reference_frame: { pending_path: 'f.png', target: [1, 2] } },
    { tool: 'type_text', text_input: '中文 🙂' },
    { tool: 'hotkey', keys: ['ctrl', 's'], repeat: 1 },
    { tool: 'ext.delay', seconds: 0.4 },
    { tool: 'ctrl.loop', loop_type: 'count', count: 2, body: [{ tool: 'hotkey', keys: ['tab'] }] },
    { tool: 'ctrl.if', cond: { window_appears: '另存为' }, then: [{ tool: 'ext.delay', seconds: 1 }], else: [] },
  ],
  variables: {},
  runtime: { kind: 'wps_replay' },
}

const loaded = codec.fromDocument(recorded)
check('a recorded document loads onto the canvas', Array.isArray(loaded.nodes) && loaded.nodes.length === 10, String(loaded.nodes && loaded.nodes.length) + ' nodes')
check('the pipeline keeps its sentinels', loaded.nodes[0].kind === 'start' && loaded.nodes[loaded.nodes.length - 1].kind === 'end')
check(
  'an unfamiliar control-flow step becomes one recorded fragment',
  loaded.nodes.some((node) => node.kind === 'replay_step' && node.params.step.tool === 'ctrl.loop'),
  loaded.nodes.map((node) => node.kind).join(','),
)
check(
  'a trailing ctrl.if becomes an editable condition node',
  loaded.nodes.filter((node) => node.kind === 'condition').length === 1,
  loaded.nodes.map((node) => node.kind).join(','),
)
const clickNode = loaded.nodes.filter((node) => node.kind === 'click')[0]
check(
  'a recorded field the panel does not own is preserved, not dropped',
  clickNode && clickNode.params.extra && clickNode.params.extra.reference_frame
    && clickNode.params.extra.reference_frame.pending_path === 'f.png',
  canonical(clickNode && clickNode.params.extra),
)

const reloaded = codec.toDocument({ id: loaded.id, name: loaded.name, created: loaded.created, task: loaded.task, conditions: loaded.conditions, evidence: loaded.evidence, variables: loaded.variables, nodes: loaded.nodes })
check(
  'the reloaded document equals the recorded one, apart from the two normalized defaults',
  canonical(normalized(reloaded.steps)) === canonical(normalized(recorded.steps)),
  'recorded=' + canonical(normalized(recorded.steps)) + ' reloaded=' + canonical(normalized(reloaded.steps)),
)
check('the reloaded envelope keeps identity and name', reloaded.id === recorded.id && reloaded.name === recorded.name && reloaded.created === recorded.created)
check('the reloaded envelope declares the offline replay runtime', reloaded.format === 'automation-ir/1' && reloaded.runtime.kind === 'wps_replay' && reloaded.source.kind === 'wps')

// A second pass must be a fixed point, so an edit-save cycle cannot drift.
const second = codec.fromDocument(reloaded)
const twice = codec.toDocument({
  id: second.id,
  name: second.name,
  created: second.created,
  task: second.task,
  conditions: second.conditions,
  evidence: second.evidence,
  variables: second.variables,
  nodes: second.nodes,
})
check('a second round trip is a fixed point', canonical(twice) === canonical(reloaded), 'drift detected')

// A canvas flow is a document the runtime can validate.
const fresh = codec.toDocument({ name: 'fresh', nodes: codec.layoutNodes([{ kind: 'wait', params: { seconds: 1 } }]) })
check(
  'a fresh canvas document carries every field the IR envelope requires',
  fresh.format === 'automation-ir/1' && fresh.id && fresh.name
    && typeof fresh.source === 'object' && typeof fresh.conditions === 'object'
    && typeof fresh.evidence === 'object' && typeof fresh.variables === 'object'
    && Array.isArray(fresh.steps) && fresh.steps.every((step) => typeof step.tool === 'string'),
  canonical(Object.keys(fresh)),
)

let refused = null
try { codec.fromDocument({ format: 'automation-ir/1', runtime: { kind: 'arp_graph' }, steps: [] }) } catch (error) { refused = error }
check('a document the canvas cannot represent is refused, not flattened', refused !== null, refused ? String(refused.message) : 'no error')

let badFormat = null
try { codec.fromDocument({ format: 'something/else' }) } catch (error) { badFormat = error }
check('an unknown format is refused', badFormat !== null)

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
check('edge layer rendered as svg', markup.indexOf('<svg') !== -1 && count(markup, '<path') === 3, count(markup, '<path') + ' paths')
check('the default pipeline renders its three sentinel-and-step nodes', count(markup, 'data-lingdong-node=') === 4, String(count(markup, 'data-lingdong-node=')))
check('palette offers the offline node vocabulary', count(markup, 'data-lingdong-palette=') === 8, String(count(markup, 'data-lingdong-palette=')))
check('recorded fragment node is in the palette', markup.indexOf('data-lingdong-palette="replay_step"') !== -1)
check('agent-semantic nodes are absent', markup.indexOf('data-lingdong-palette="agent_goal"') === -1 && markup.indexOf('data-lingdong-palette="skill_replay"') === -1)
check('canvas surface rendered', markup.indexOf('data-lingdong-canvas') !== -1)
check('the flow library panel rendered', markup.indexOf('data-lingdong-canvas') !== -1 && markup.indexOf('\u6d41\u7a0b\u5e93') !== -1)
check('the run control is present', markup.indexOf('data-lingdong-run="1"') !== -1)
check('the file name field is present', markup.indexOf('data-lingdong-field="file"') !== -1)
check('the canvas never claims a model call', markup.indexOf('0 \u6a21\u578b\u8c03\u7528') !== -1, 'expected the model-call counter in the status bar')
check('the status bar reports the resting run state', markup.indexOf('data-lingdong-run-state="idle"') !== -1)
check('the inspector starts empty', markup.indexOf('\u9009\u4e2d\u4e00\u4e2a\u8282\u70b9') !== -1)

console.log('')
console.log(failures.length === 0 ? 'ALL CHECKS PASSED' : failures.length + ' CHECK(S) FAILED: ' + failures.join(', '))
process.exitCode = failures.length === 0 ? 0 : 1
