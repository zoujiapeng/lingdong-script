/**
 * Model-facing tool surface for the Lingdong computer-use provider.
 *
 * Every desktop mutation is executed by the repository's canonical ARP pipeline,
 * so the same physical single-writer and release-cleanup path as the standalone
 * product applies. Nothing here re-implements input.
 * @module dsh-plugin-lingdong/tools
 */
import os from 'node:os'
import path from 'node:path'
import { readFile } from 'node:fs/promises'

/** One canonical action batch expressed as a minimal ARP graph. */
export function actionBatchGraph({ actions, name = 'DSH action batch', id }) {
  const graphId = id || `dsh-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
  return {
    format: 'arp/1',
    id: graphId,
    name,
    description: 'Submitted by the DSH lingdong plugin',
    nodes: [
      { id: 'start', kind: 'start', name: 'start', config: {}, x: 0, y: 0 },
      {
        id: 'actions',
        kind: 'action_batch',
        name,
        config: { actions, subgoal: name, stop_on_error: true },
        x: 200,
        y: 0,
      },
      { id: 'end', kind: 'end', name: 'end', config: {}, x: 400, y: 0 },
    ],
    edges: [
      { id: 'e-start', source: 'start', target: 'actions', branch: 'next', label: '' },
      { id: 'e-end', source: 'actions', target: 'end', branch: 'next', label: '' },
    ],
    variables: {},
    policy: { max_node_visits: 20, fail_fast: true },
    metadata: { source: 'dsh-plugin-lingdong' },
  }
}

/**
 * Prepend a target-establishing focus action when a window is named.
 *
 * The runtime's graph validator does NOT require a target: the
 * "establish a window before a spatial action" rule lives in the flow editor,
 * not in the backend contract. Coordinates are absolute screen pixels, so a
 * named target is what makes a pointer or typing batch deterministic.
 * @param actions - declared action list.
 * @param window - exact window title, when the caller named one.
 * @returns a new action list with the focus action first.
 */
export function withTargetWindow(actions, window) {
  const list = Array.isArray(actions) ? [...actions] : []
  if (window) list.unshift({ action: 'focus_window', title: String(window) })
  return list
}

/** Depth-first search for the first object carrying an artifact identity. */
export function findArtifact(value, seen = new Set()) {
  if (!value || typeof value !== 'object' || seen.has(value)) return null
  seen.add(value)
  if (typeof value.artifact_id === 'string' && value.artifact_id) return value
  const children = Array.isArray(value) ? value : Object.values(value)
  for (const child of children) {
    const hit = findArtifact(child, seen)
    if (hit) return hit
  }
  return null
}

/** Wrap a JSON value as an MCP-compatible tool result. */
export function toolResult(value, images = []) {
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2)
  const content = [{ type: 'text', text }]
  for (const image of images) content.push(image)
  const result = { content }
  if (value && typeof value === 'object' && !Array.isArray(value)) result.structuredContent = value
  return result
}

const ACTION_BATCH_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['actions'],
  properties: {
    window: {
      type: 'string',
      description:
        'Exact window title to focus before the batch runs. Strongly preferred for any batch that moves the pointer or ' +
        'types: coordinates are absolute screen pixels, and focusing the intended window is what makes them deterministic.',
    },
    stop_on_error: { type: 'boolean', description: 'Abort the batch on the first failed action. Defaults to true.' },
    actions: {
      type: 'array',
      minItems: 1,
      description:
        'Canonical desktop actions executed in order. Supported action names: ' +
        'observe, click, click_element, type_into_element, type_text, hotkey, scroll, move_mouse, ' +
        'hold_key, drag, draw_strokes, focus_window, launch, wait, wait_change, wait_for_visual_change, ' +
        'wait_stable, read_text, zoom, screenshot, clipboard_read, clipboard_write, clipboard_paste.',
      items: { type: 'object', additionalProperties: true },
    },
  },
}

/**
 * Run one bounded sequence of feature calls and stop at the first failure.
 *
 * Several tool actions are really a short ordered sequence - read the title,
 * then the URL, then the body - and a caller wants one answer with the first
 * failure located, not three separate round trips it has to reconcile.
 * @param browser - the browser session.
 * @param steps - `[featureName, params]` pairs, a falsy name skipping a step.
 * @returns `{ ok, steps, error }`.
 */
export async function browserSteps(browser, steps) {
  const results = []
  for (const [name, params] of steps) {
    if (!name) continue
    const answer = await browser.call(name, params)
    results.push({ feature: name, ok: answer.ok, result: answer.result ?? null, error: answer.error || '' })
    if (!answer.ok) break
  }
  const failed = results.find((entry) => !entry.ok)
  return { ok: !failed, steps: results, error: failed ? failed.error : '' }
}

/**
 * Build the model-facing tool list.
 * @param options - client, settings, runtime handle and the browser session.
 * @returns tool descriptors consumed by the Cordis mount.
 */
export function buildTools({ client, sidecar, settings, browser = null }) {
  async function submitBatch(args, { name = 'DSH action batch' } = {}) {
    const actions = withTargetWindow(args.actions, args.window)
    const graph = actionBatchGraph({ actions, name })
    const submitted = await client.post('/api/v2/arp/runs', { graph, inputs: {} })
    const runId = submitted.run?.run_id ?? submitted.run_id
    if (!runId) throw new Error('runtime accepted the batch but returned no run id')
    const final = await client.waitForRun(runId, { timeoutMs: settings.requestTimeoutMs })
    return { run_id: runId, status: client.statusOf(final), run: final.run ?? null, arp: final.arp ?? null, graph_id: graph.id }
  }

  return [
    {
      name: 'lingdong_status',
      description:
        'Report the Lingdong desktop runtime state: reachability, build identity and loaded configuration. ' +
        'Call this before the first desktop action of a session.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {} },
      async handler() {
        const ping = await client.get('/api/ping', { timeoutMs: 15_000 })
        let status = null
        try { status = await client.get('/api/status', { timeoutMs: 15_000 }) } catch (error) { status = { error: String(error.message || error) } }
        return toolResult({ base_url: sidecar.baseUrl, attached: sidecar.external, ping, status })
      },
    },

    {
      name: 'lingdong_windows',
      description:
        'List the visible windows and background applications the runtime can target. Use it to discover the exact ' +
        'window title to pass to lingdong_desktop, instead of guessing one.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {} },
      async handler() {
        return toolResult(await client.get('/api/v2/desktop/windows', { timeoutMs: 60_000 }))
      },
    },

    {
      name: 'lingdong_screen',
      description:
        'Report the virtual-screen origin and size in physical pixels. Coordinates accepted by lingdong_desktop are ' +
        'physical screen pixels, so this is the frame that bounds them.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {} },
      async handler() {
        return toolResult(await client.get('/api/v2/desktop/screen', { timeoutMs: 30_000 }))
      },
    },

    {
      name: 'lingdong_cursor',
      description: 'Report the current physical cursor position.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {} },
      async handler() {
        return toolResult(await client.get('/api/v2/desktop/cursor', { timeoutMs: 30_000 }))
      },
    },

    {
      name: 'lingdong_apps',
      description:
        'Discover installed applications by human name, or list the bounded index. Use it before lingdong_desktop ' +
        'launches anything, so the launch target is one the runtime can actually resolve.',
      inputSchema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          query: { type: 'string', description: 'Human application name. Empty lists the index instead.' },
          max_results: { type: 'number', description: 'Bound on returned entries (1-200, default 20).' },
          refresh: { type: 'boolean', description: 'Rebuild the discovery index instead of using its cache.' },
        },
      },
      async handler(args) {
        const params = new URLSearchParams()
        if (args.query) params.set('query', String(args.query))
        if (args.max_results != null) params.set('max_results', String(args.max_results))
        if (args.refresh) params.set('refresh', 'true')
        const suffix = params.toString()
        return toolResult(
          await client.get(`/api/v2/desktop/apps${suffix ? `?${suffix}` : ''}`, { timeoutMs: 120_000 }),
        )
      },
    },

    {
      name: 'lingdong_elements',
      description:
        'Read the accessibility elements of one window, when the UIA backend is available. Prefer this over pixel ' +
        'guessing for standard controls; fall back to lingdong_observe when the window is custom-drawn. ' +
        'Use lingdong_snapshot instead when you also need to see the window.',
      inputSchema: {
        type: 'object',
        additionalProperties: false,
        required: ['hwnd'],
        properties: {
          hwnd: { type: 'number', description: 'Window handle from lingdong_windows.' },
          limit: { type: 'number', description: 'Bound on returned elements (1-200, default 200).' },
        },
      },
      async handler(args) {
        const params = new URLSearchParams({ hwnd: String(args.hwnd) })
        if (args.limit != null) params.set('limit', String(args.limit))
        return toolResult(await client.get(`/api/v2/desktop/elements?${params}`, { timeoutMs: 60_000 }))
      },
    },

    {
      name: 'lingdong_snapshot',
      description:
        'Take one window as both an image and an accessibility tree, captured together, and return the image. ' +
        'This is the window-level view: it tells you exactly which frame the pixels came from, so element ' +
        'rectangles and the picture agree. One window image may be clearer than the whole screen for small ' +
        'controls. A minimized window has no meaningful pixels and reports why instead of returning a stale frame.',
      inputSchema: {
        type: 'object',
        additionalProperties: false,
        required: ['hwnd'],
        properties: {
          hwnd: { type: 'number', description: 'Window handle from lingdong_windows.' },
          limit: { type: 'number', description: 'Bound on returned elements (1-200, default 200).' },
          max_side: { type: 'number', description: 'Longest edge of the returned image in pixels (64-2048, default 1120).' },
        },
      },
      async handler(args) {
        const params = new URLSearchParams({ hwnd: String(args.hwnd) })
        if (args.limit != null) params.set('limit', String(args.limit))
        if (args.max_side != null) params.set('max_side', String(args.max_side))
        const snapshot = await client.get(`/api/v2/desktop/snapshot?${params}`, { timeoutMs: 60_000 })
        const image = snapshot?.image ?? null
        const meta = image
          ? {
              content_type: image.content_type,
              width: image.width,
              height: image.height,
              bytes: image.bytes,
              sha256: image.sha256,
            }
          : null
        const body = { ...snapshot, image: meta }
        if (!image?.base64) return toolResult(body)
        return toolResult(body, [
          { type: 'image', data: image.base64, mimeType: image.content_type || 'image/jpeg' },
        ])
      },
    },

    {
      name: 'lingdong_verify',
      description:
        'Evaluate a bounded predicate set against the live desktop and report whether it holds. This is the ' +
        'runtime\'s own evaluator, the same one used before and after an offline step, so its answer is a real ' +
        'check rather than a restatement of what you asked for. Use it to confirm an outcome instead of assuming ' +
        'a delivered action worked. Supported conditions: text_appears, text_gone, window_appears, window_gone, ' +
        'window_title, screen_stable, color_match, file_exists, file_format, file_contains, process_running, ' +
        'process_gone, shell_true. Note that shell_true runs a command and reads its exit status.',
      inputSchema: {
        type: 'object',
        additionalProperties: false,
        required: ['expect'],
        properties: {
          expect: {
            type: 'object',
            description: 'Condition object, for example {"window_appears": "Save as"} or {"file_exists": "C:/tmp/a.txt"}.',
          },
          hwnd: { type: 'number', description: 'Window to evaluate visual conditions against, from lingdong_windows.' },
          timeout_s: { type: 'number', description: 'How long to keep polling before answering false (0.2-120, default 6).' },
        },
      },
      async handler(args) {
        return toolResult(
          await client.post(
            '/api/v2/desktop/verify',
            { expect: args.expect, ...(args.hwnd != null ? { hwnd: args.hwnd } : {}), ...(args.timeout_s != null ? { timeout_s: args.timeout_s } : {}) },
            { timeoutMs: 180_000 },
          ),
        )
      },
    },

    {
      name: 'lingdong_observe',
      description:
        'Capture the current real desktop and return the image. Always observe before deciding coordinates, ' +
        'and re-observe after every mutation because a delivered action does not prove the outcome.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {} },
      async handler() {
        const result = await submitBatch(
          {
            actions: [
              {
                action: 'screenshot',
                managed_artifact: true,
                scope: 'screen',
                purpose: 'user_output',
                sendable: true,
              },
            ],
          },
          { name: 'DSH observe' },
        )
        const artifact = findArtifact(result.arp ?? result.run)
        if (!artifact?.artifact_id) return toolResult({ ...result, image: null, note: 'screenshot produced no managed artifact' })
        const image = await client.get(
          `/api/v2/runs/${encodeURIComponent(result.run_id)}/artifacts/${encodeURIComponent(artifact.artifact_id)}`,
          { raw: true, timeoutMs: 60_000 },
        )
        return toolResult(
          { ...result, artifact: { ...artifact, bytes: image.bytes.length, content_type: image.contentType } },
          [{ type: 'image', data: image.bytes.toString('base64'), mimeType: image.contentType }],
        )
      },
    },

    {
      name: 'lingdong_desktop',
      description:
        'Execute canonical keyboard, mouse and window actions on the real Windows desktop in one ordered batch. ' +
        'This is the primitive surface: it performs the input but never judges whether the user goal succeeded. ' +
        'Pass the exact window title whenever the batch moves the pointer or types, then verify with lingdong_observe.',
      inputSchema: ACTION_BATCH_SCHEMA,
      async handler(args) {
        return toolResult(await submitBatch(args))
      },
    },

    {
      name: 'lingdong_run_goal',
      description:
        'Hand a natural-language desktop goal to the Lingdong visual agent, which observes the real screen, ' +
        'acts with the mouse and keyboard, and reports what it saw. Use this for open-ended GUI work whose steps ' +
        'are not known in advance; use lingdong_desktop when the exact actions are already decided.',
      inputSchema: {
        type: 'object',
        additionalProperties: false,
        required: ['goal'],
        properties: {
          goal: { type: 'string', description: 'The complete user goal in natural language.' },
          thread_id: { type: 'string', description: 'Optional conversation identity to continue a previous goal.' },
          timeout_ms: { type: 'number', description: 'Polling budget before returning partial progress.' },
        },
      },
      async handler(args) {
        const submitted = await client.post('/api/v2/goals/runs', {
          goal: String(args.goal),
          ...(args.thread_id ? { thread_id: String(args.thread_id) } : {}),
        })
        const runId = submitted.run?.run_id ?? submitted.run_id
        if (!runId) return toolResult({ submitted })
        const final = await client.waitForRun(runId, { timeoutMs: args.timeout_ms ?? settings.requestTimeoutMs })
        return toolResult({
          run_id: runId,
          status: client.statusOf(final),
          reply: final.run?.result ?? final.run?.reply ?? null,
          run: final.run ?? null,
        })
      },
    },

    {
      name: 'lingdong_run_flow',
      description:
        'Execute a saved or inline Lingdong Automation IR flow offline (no model calls). Replay segments and ' +
        'deterministic script nodes run in one session, sharing variables. This is how a recorded action sequence ' +
        'is re-executed together with hand-written script steps.',
      inputSchema: {
        type: 'object',
        additionalProperties: false,
        required: ['automation'],
        properties: {
          automation: { type: 'object', description: 'An automation-ir/1 document.' },
          inputs: { type: 'object', description: 'Optional flow input variables.' },
        },
      },
      async handler(args) {
        const submitted = await client.post('/api/v2/automation-ir/runs', {
          automation: args.automation,
          inputs: args.inputs ?? {},
        })
        if (submitted.replay_id) {
          const final = await client.get(`/api/replay/${encodeURIComponent(submitted.replay_id)}`)
          return toolResult({ runtime_kind: submitted.runtime_kind, replay_id: submitted.replay_id, result: final })
        }
        const runId = submitted.run_id ?? submitted.run?.run_id
        if (!runId) return toolResult({ submitted })
        const final = await client.waitForRun(runId)
        return toolResult({ runtime_kind: submitted.runtime_kind, run_id: runId, status: client.statusOf(final), run: final.run ?? null })
      },
    },

    {
      name: 'lingdong_flow_files',
      description:
        'List, read or write the saved Lingdong flow/script documents the desktop runtime can execute. ' +
        'Use this to persist a flow produced from a recording or from lingdong_run_goal.',
      inputSchema: {
        type: 'object',
        additionalProperties: false,
        required: ['action'],
        properties: {
          action: { type: 'string', enum: ['list', 'read', 'write'] },
          file: { type: 'string', description: 'File name inside the runtime scripts directory.' },
          content: { type: 'object', description: 'Automation IR document to write.' },
        },
      },
      async handler(args) {
        if (args.action === 'list') return toolResult(await client.get('/api/scripts'))
        if (!args.file) throw new Error('file is required for read/write')
        const file = encodeURIComponent(String(args.file))
        if (args.action === 'read') return toolResult(await client.get(`/api/script/${file}`))
        if (!args.content) throw new Error('content is required for write')
        return toolResult(await client.put(`/api/script/${file}`, args.content))
      },
    },

    {
      name: 'lingdong_record',
      description:
        'Start, stop or inspect a human demonstration recording. While enabled, real mouse and keyboard input ' +
        'is captured as a replayable sequence. This is the source of the replay segments that workflows embed.',
      inputSchema: {
        type: 'object',
        additionalProperties: false,
        required: ['action'],
        properties: {
          action: { type: 'string', enum: ['start', 'stop', 'status'] },
          name: { type: 'string', description: 'Optional stable name for the produced recording.' },
        },
      },
      async handler(args) {
        if (args.action === 'status') return toolResult(await client.get('/api/v2/replay/apprentice/status'))
        const body = args.name ? { name: String(args.name) } : {}
        return toolResult(await client.post(`/api/v2/replay/apprentice/${args.action}`, body))
      },
    },

    {
      name: 'lingdong_export',
      description:
        'Convert an observed agent run, or a whole conversation thread, into an offline Automation IR document. ' +
        'The result can be loaded into a flow and re-executed with zero model calls. Reports partial coverage ' +
        'explicitly instead of silently dropping actions.',
      inputSchema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          run_id: { type: 'string', description: 'Export one run.' },
          thread_id: { type: 'string', description: 'Export every ordinary action recorded for a conversation.' },
        },
      },
      async handler(args) {
        if (args.thread_id) {
          return toolResult(await client.post(`/api/export/thread/${encodeURIComponent(String(args.thread_id))}/ir`, {}))
        }
        if (!args.run_id) throw new Error('run_id or thread_id is required')
        return toolResult(await client.post(`/api/export/${encodeURIComponent(String(args.run_id))}/ir`, {}))
      },
    },
  ]
}

/** Guidance injected into the system prompt while this provider is mounted. */
export const GUIDANCE = `The Lingdong computer-use tools operate the real Windows desktop through a single physical input queue. Prefer lingdong_desktop when you already know the actions, and lingdong_run_goal when you do not. Observe before choosing coordinates and re-observe after every mutation: a delivered click is not evidence of the intended outcome - lingdong_verify is. lingdong_snapshot returns one window's pixels and accessibility tree from the same moment, which is the better view for a small control. Pass the exact window title for any batch that moves the pointer or types. Recordings and exported runs become offline replay segments that can be executed together with script steps through lingdong_run_flow, with no model calls.`

/** Guidance for the browser session, added only when that surface is mounted. */
export const BROWSER_GUIDANCE = `The Lingdong browser session drives a Chromium-family browser over CDP, with no pixels and no pointer involved: selectors, page text and page state, which is far more reliable than clicking coordinates when the target is a web page. It has no session until you call lingdong_browser with action launch or connect; it never opens a window on its own.`

/**
 * Build the browser tool descriptor.
 *
 * The action surface is deliberately one tool rather than twenty: a browser
 * session is stateful, and a model that has to remember which of twenty tools
 * needs a session will eventually call one that does not exist yet.
 * @param browser - the session owner.
 * @returns one tool descriptor.
 */
export function buildBrowserTool(browser) {
  return {
    name: 'lingdong_browser',
    description:
      'Drive a Chromium-family browser over CDP: navigate, read the page, find and interact with elements by CSS ' +
      'selector or text, run JavaScript, wait for conditions, and take page screenshots. This works on the page ' +
      'itself, not on pixels, so it is the reliable way to operate a web page. Start with action "launch" (the ' +
      'plugin starts a browser it owns) or action "connect" (attach to a browser you already started with ' +
      '--remote-debugging-port). Every other action is refused until one of those has succeeded, because this ' +
      'tool never opens a window on its own.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      required: ['action'],
      properties: {
        action: {
          type: 'string',
          enum: [
            'status', 'launch', 'connect', 'close', 'open', 'new_tab', 'tabs', 'switch', 'close_tab',
            'read', 'find', 'click', 'type', 'press', 'select', 'hover', 'scroll', 'wait', 'eval',
            'screenshot', 'upload', 'download',
          ],
          description: 'Which browser operation to perform.',
        },
        url: { type: 'string', description: 'URL for open / new_tab. A bare domain gets https://.' },
        selector: { type: 'string', description: 'CSS selector for the element actions.' },
        text: { type: 'string', description: 'Element text to match when no selector is given.' },
        value: { type: 'string', description: 'Text for type, value for select.' },
        label: { type: 'string', description: 'Option label for select, when value is not used.' },
        key: { type: 'string', description: 'Key name for press, for example Enter or Control+a.' },
        code: { type: 'string', description: 'JavaScript for eval. Use "return ..." to produce a value.' },
        condition: {
          type: 'string',
          description: 'Wait condition for wait: text_visible, text_hidden, element_visible, element_hidden, url_contains, title_contains, page_stable.',
        },
        target: { type: 'string', description: 'Target tab for switch: its title or its 1-based index.' },
        path: { type: 'string', description: 'Absolute path for screenshot, upload or download.' },
        full_page: { type: 'boolean', description: 'Capture the whole page rather than the viewport.' },
        timeout: { type: 'number', description: 'Seconds to wait, for click / type / wait.' },
        port: { type: 'number', description: 'CDP port for launch / connect. Defaults to the configured port.' },
        headless: { type: 'boolean', description: 'Whether launch starts a headless browser. Defaults to true.' },
        max_results: { type: 'number', description: 'Bound on elements returned by find (default 20).' },
        attributes: { type: 'boolean', description: 'Include each found element attribute map in the result.' },
      },
    },
    async handler(args) {
      const action = String(args.action || '')
      const timeout = args.timeout != null ? Number(args.timeout) : undefined
      const element = args.selector ? { selector: String(args.selector) } : { text: String(args.text || '') }
      if (action === 'status') {
        return toolResult({ ok: true, ...browser.facts() })
      }
      if (action === 'launch') {
        return toolResult({ ok: true, ...(await browser.launch({ headless: args.headless !== false, ...(args.port != null ? { port: Number(args.port) } : {}) })) })
      }
      if (action === 'connect') {
        return toolResult({ ok: true, ...(await browser.connect(args.port != null ? { port: Number(args.port) } : {})) })
      }
      if (action === 'close') {
        await browser.close()
        return toolResult({ ok: true, closed: true, ...browser.facts() })
      }
      if (action === 'open') {
        return toolResult(await browserSteps(browser, [
          ['browser_navigate', { url: String(args.url || ''), wait_until: 'domcontentloaded' }],
        ]))
      }
      if (action === 'new_tab') {
        // The session tracks one active page, and the helper's own new-tab call
        // does not move it, so the tab is selected explicitly afterwards.
        return toolResult(await browserSteps(browser, [
          ['browser_new_tab', { url: String(args.url || 'about:blank') }],
          ['browser_switch_tab', { target: '1' }],
        ]))
      }
      if (action === 'tabs') {
        return toolResult(await browserSteps(browser, [['browser_list_tabs', {}]]))
      }
      if (action === 'switch') {
        return toolResult(await browserSteps(browser, [['browser_switch_tab', { target: String(args.target || '1') }]]))
      }
      if (action === 'close_tab') {
        return toolResult(await browserSteps(browser, [['browser_close_tab', { target: String(args.target || '') }]]))
      }
      if (action === 'read') {
        const steps = [['browser_get_title', {}], ['browser_get_url', {}], ['browser_get_text', { selector: String(args.selector || 'body') }]]
        const answer = await browserSteps(browser, steps)
        const [title, url, body] = answer.steps.map((entry) => (entry.ok ? entry.result : null))
        return toolResult({ ok: answer.ok, title, url, text: body, steps: answer.steps, error: answer.error })
      }
      if (action === 'find') {
        return toolResult(await browserSteps(browser, [['browser_find_elements', {
          selector: String(args.selector || 'a, button, input'),
          max_results: args.max_results != null ? Number(args.max_results) : 20,
        }]]))
      }
      if (action === 'click') {
        return toolResult(await browserSteps(browser, [['browser_click', { ...element, timeout: timeout ?? 10 }]]))
      }
      if (action === 'type') {
        if (!args.selector) throw new Error('type needs a selector for the field to fill')
        return toolResult(await browserSteps(browser, [['browser_type', {
          selector: String(args.selector),
          text: String(args.value ?? ''),
          clear_first: true,
        }]]))
      }
      if (action === 'press') {
        return toolResult(await browserSteps(browser, [['browser_press_key', { key: String(args.key || 'Enter') }]]))
      }
      if (action === 'select') {
        return toolResult(await browserSteps(browser, [['browser_select_option', {
          selector: String(args.selector || 'select'),
          ...(args.value != null ? { value: String(args.value) } : {}),
          ...(args.label != null ? { label: String(args.label) } : {}),
        }]]))
      }
      if (action === 'hover') {
        return toolResult(await browserSteps(browser, [['browser_hover', element]]))
      }
      if (action === 'scroll') {
        return toolResult(await browserSteps(browser, [['browser_scroll_to', { ...element, behavior: 'smooth' }]]))
      }
      if (action === 'wait') {
        return toolResult(await browserSteps(browser, [['browser_wait_for', {
          condition: String(args.condition || 'element_visible'),
          target: String(args.target || args.selector || args.text || ''),
          timeout: timeout ?? 30,
        }]]))
      }
      if (action === 'eval') {
        return toolResult(await browserSteps(browser, [['browser_execute_js', {
          code: String(args.code || ''),
          selector: String(args.selector || ''),
        }]]))
      }
      if (action === 'screenshot') {
        const target = String(args.path || path.join(os.tmpdir(), `lingdong-page-${Date.now()}.png`))
        const answer = await browserSteps(browser, [['browser_screenshot', {
          selector: String(args.selector || ''),
          full_page: args.full_page === true,
          path: target,
        }]])
        const captured = answer.steps[0]?.result
        if (!answer.ok || !captured?.path) return toolResult(answer)
        // The page, as an image: a model that asked to look at the page should
        // get the page, not only a file name it cannot open.
        let bytes = null
        try { bytes = await readFile(String(captured.path)) } catch { bytes = null }
        if (!bytes) return toolResult({ ...answer, note: 'the capture was written but could not be read back' })
        return toolResult({ ...answer, image: { ...captured, bytes: bytes.length, content_type: 'image/png' } }, [
          { type: 'image', data: bytes.toString('base64'), mimeType: 'image/png' },
        ])
      }
      if (action === 'upload') {
        if (!args.selector) throw new Error('upload needs the selector of the file input')
        if (!args.path) throw new Error('upload needs the absolute path of the file to send')
        // The page's file input is set directly, so no native file picker opens
        // and the desktop's physical input queue is never involved.
        return toolResult(await browserSteps(browser, [['browser_upload_file', {
          selector: String(args.selector),
          files: JSON.stringify([String(args.path)]),
          timeout: timeout ?? 30,
        }]]))
      }
      if (action === 'download') {
        return toolResult(await browserSteps(browser, [['browser_download', {
          selector: String(args.selector || ''),
          ...(args.path ? { save_path: String(args.path) } : {}),
        }]]))
      }
      throw new Error(`unsupported browser action: ${action}`)
    },
  }
}

