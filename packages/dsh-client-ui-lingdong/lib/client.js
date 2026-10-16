/**
 * Lingdong flow workbench - DSH Web GUI client plugin.
 *
 * Bundle format: DSH loads client plugins through window.__ModuleLoader__.load,
 * which hands the factory a require() resolving host modules. That wrapper is
 * the entire artifact, so this file is written by hand and no bundler is in
 * the loop (matching how DSH ships its own client bundles).
 *
 * Slice 3 connects the canvas to the host. The pipeline the canvas draws is the
 * `steps` list of an `automation-ir/1` document with `runtime.kind =
 * "wps_replay"`, which is exactly the document a recorded demonstration exports
 * and exactly the document the offline replayer executes. Opening an exported
 * recording and saving an edited flow are therefore the same code path, and one
 * node compiles to one `.wps.json` step with no translation layer in between.
 *
 * Every node kind compiles exactly. A step this editor does not own - an
 * unfamiliar tool, a `ctrl.loop`, a `ctrl.if` with a real else-branch - is kept
 * as an opaque recorded fragment that round-trips field for field, rather than
 * being flattened into parameters that would silently mean something else.
 */
window.__ModuleLoader__.load({
	id: "@lingdong/dsh-client-ui-lingdong",
	factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;
		Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
		const react = require("react");
		const jsxRuntime = require("react/jsx-runtime");
		const rawJsx = jsxRuntime.jsx;
		const rawJsxs = jsxRuntime.jsxs;

		/**
		 * React 19 takes an element's key as the runtime's third argument and warns
		 * when it arrives inside the props object. Every element in this file is
		 * written with the key among its props for readability, so it is moved here,
		 * once, instead of at each of the hundred call sites.
		 */
		function jsx(type, props, key) {
			const copy = Object.assign({}, props);
			const inline = copy.key;
			delete copy.key;
			return rawJsx(type, copy, key !== undefined ? key : inline);
		}

		/** {@link jsx} for elements with more than one child. */
		function jsxs(type, props, key) {
			const copy = Object.assign({}, props);
			const inline = copy.key;
			delete copy.key;
			return rawJsxs(type, copy, key !== undefined ? key : inline);
		}

		const NS = "@lingdong/dsh-client-ui-lingdong";
		const TAB_ID = "@lingdong/dsh-client-ui-lingdong";
		const TAB_KIND = "lingdong-flow";
		const ADDRESS_PREFIX = "dsh-resource://lingdong-flow/";

		/** The host half of this plugin, reached same-origin through the GUI. */
		const BRIDGE = "/lingdong/api";

		/** Client services this plugin consumes. */
		const inject = ["slots", "locale", "sidebarRightTabs"];

		const dictionaries = {
			zh: {
				"tab.title": "流程工作台",
				"toolbar.new": "新建",
				"toolbar.refresh": "刷新",
				"toolbar.save": "保存",
				"toolbar.saveAs": "另存为",
				"toolbar.run": "运行",
				"toolbar.stop": "停止",
				"toolbar.delete": "删除",
				"toolbar.zoomIn": "放大",
				"toolbar.zoomOut": "缩小",
				"toolbar.reset": "重置视图",
				"library.title": "流程库",
				"library.loading": "读取中…",
				"library.empty": "还没有保存的流程",
				"library.error": "无法读取流程库",
				"library.count": "{{count}} 个流程",
				"palette.title": "节点库",
				"palette.hint": "点击追加到末尾；已选中节点时插到它后面",
				"inspector.title": "属性",
				"inspector.empty": "选中一个节点后在此编辑。",
				"inspector.file": "文件名",
				"inspector.fileHint": "保存到脚本目录，必须以 .automation.json 结尾。",
				"inspector.label": "名称",
				"inspector.kind": "类型",
				"inspector.params": "参数",
				"inspector.position": "位置",
				"inspector.moveUp": "上移",
				"inspector.moveDown": "下移",
				"inspector.window": "目标窗口",
				"inspector.windowHint": "标题包含即匹配；填写后重放会先切到该窗口，坐标点击才确定。",
				"inspector.x": "X（物理像素）",
				"inspector.y": "Y（物理像素）",
				"inspector.button": "鼠标键",
				"inspector.double": "双击",
				"inspector.text": "文本",
				"inspector.keys": "按键",
				"inspector.keysHint": "例如 ctrl+s、enter、alt+f4",
				"inspector.seconds": "秒",
				"inspector.conditionKey": "条件",
				"inspector.conditionValue": "取值",
				"inspector.conditionOpaque": "这条条件不能精确编辑，按原样保留；可在片段 JSON 中修改。",
				"inspector.timeout": "超时（秒）",
				"inspector.path": "保存路径",
				"inspector.pathHint": "相对脚本目录；留空用默认 shots/replay.png",
				"inspector.command": "命令",
				"inspector.shell": "Shell",
				"inspector.stepJson": "片段步骤（JSON）",
				"inspector.preserved": "录制字段",
				"inspector.preservedHint": "这些字段不由本面板编辑，保存时原样写回。",
				"status.nodes": "节点",
				"status.edges": "连线",
				"status.modelCalls": "模型调用",
				"status.unsaved": "未保存",
				"status.untitled": "未命名流程",
				"status.dirty": "有未保存改动",
				"run.idle": "未运行",
				"run.validating": "运行时校验中…",
				"run.submitting": "提交中…",
				"run.running": "运行中",
				"run.done": "完成",
				"run.failed": "失败",
				"run.stopped": "已停止",
				"run.invalid": "运行时校验未通过，未提交运行",
				"run.skipped": "未执行",
				"run.taken": "条件成立",
				"run.notTaken": "条件不成立",
				"run.stepFailed": "步骤失败",
				"notice.saved": "已保存",
				"notice.opened": "已打开",
				"notice.created": "已新建流程",
				"notice.loadFailed": "打开失败",
				"notice.saveFailed": "保存失败",
				"notice.runFailed": "运行失败",
				"notice.noFile": "请先填写文件名再保存",
				"notice.blocked": "该节点无法精确编译，运行已阻止",
				"action.click": "点击",
				"action.input_text": "输入文本",
				"action.keyboard": "键盘",
				"action.wait": "等待",
				"action.condition": "条件",
				"action.screenshot": "截图",
				"action.replay_step": "离线脚本片段",
				"action.run_command": "运行命令",
				"action.start": "开始",
				"action.end": "结束"
			},
			en: {
				"tab.title": "Flow Studio",
				"toolbar.new": "New",
				"toolbar.refresh": "Refresh",
				"toolbar.save": "Save",
				"toolbar.saveAs": "Save as",
				"toolbar.run": "Run",
				"toolbar.stop": "Stop",
				"toolbar.delete": "Delete",
				"toolbar.zoomIn": "Zoom in",
				"toolbar.zoomOut": "Zoom out",
				"toolbar.reset": "Reset view",
				"library.title": "Flows",
				"library.loading": "Loading…",
				"library.empty": "No saved flow yet",
				"library.error": "Cannot read the flow library",
				"library.count": "{{count}} flows",
				"palette.title": "Nodes",
				"palette.hint": "Appends to the end; with a node selected, inserts after it",
				"inspector.title": "Properties",
				"inspector.empty": "Select a node to edit it here.",
				"inspector.file": "File name",
				"inspector.fileHint": "Saved into the script library; must end in .automation.json.",
				"inspector.label": "Name",
				"inspector.kind": "Kind",
				"inspector.params": "Parameters",
				"inspector.position": "Position",
				"inspector.moveUp": "Move up",
				"inspector.moveDown": "Move down",
				"inspector.window": "Target window",
				"inspector.windowHint": "Substring match on the title; replay focuses it first so coordinates are deterministic.",
				"inspector.x": "X (physical px)",
				"inspector.y": "Y (physical px)",
				"inspector.button": "Button",
				"inspector.double": "Double click",
				"inspector.text": "Text",
				"inspector.keys": "Keys",
				"inspector.keysHint": "for example ctrl+s, enter, alt+f4",
				"inspector.seconds": "Seconds",
				"inspector.conditionKey": "Condition",
				"inspector.conditionValue": "Value",
				"inspector.conditionOpaque": "This condition cannot be edited exactly and is kept verbatim; edit it in the fragment JSON.",
				"inspector.timeout": "Timeout (s)",
				"inspector.path": "Save path",
				"inspector.pathHint": "Relative to the script directory; empty uses shots/replay.png",
				"inspector.command": "Command",
				"inspector.shell": "Shell",
				"inspector.stepJson": "Fragment step (JSON)",
				"inspector.preserved": "Recorded fields",
				"inspector.preservedHint": "This panel does not edit these; they are written back verbatim.",
				"status.nodes": "nodes",
				"status.edges": "edges",
				"status.modelCalls": "model calls",
				"status.unsaved": "unsaved",
				"status.untitled": "Untitled flow",
				"status.dirty": "unsaved changes",
				"run.idle": "not running",
				"run.validating": "validating with the runtime…",
				"run.submitting": "submitting…",
				"run.running": "running",
				"run.done": "done",
				"run.failed": "failed",
				"run.stopped": "stopped",
				"run.invalid": "the runtime rejected the flow; nothing was submitted",
				"run.skipped": "not executed",
				"run.taken": "condition true",
				"run.notTaken": "condition false",
				"run.stepFailed": "step failed",
				"notice.saved": "Saved",
				"notice.opened": "Opened",
				"notice.created": "New flow",
				"notice.loadFailed": "Open failed",
				"notice.saveFailed": "Save failed",
				"notice.runFailed": "Run failed",
				"notice.noFile": "Enter a file name before saving",
				"notice.blocked": "This node cannot be compiled exactly; the run was blocked",
				"action.click": "Click",
				"action.input_text": "Input text",
				"action.keyboard": "Keyboard",
				"action.wait": "Wait",
				"action.condition": "Condition",
				"action.screenshot": "Screenshot",
				"action.replay_step": "Recorded fragment",
				"action.run_command": "Run command",
				"action.start": "Start",
				"action.end": "End"
			}
		};

		/** Read the dictionary the document language selects. */
		function translate(key) {
			let lang = "zh";
			try {
				if (typeof document !== "undefined" && document.documentElement && document.documentElement.lang) {
					lang = String(document.documentElement.lang);
				}
			} catch (error) {
				lang = "zh";
			}
			const table = lang.toLowerCase().indexOf("en") === 0 ? dictionaries.en : dictionaries.zh;
			return table[key] != null ? table[key] : (dictionaries.zh[key] != null ? dictionaries.zh[key] : key);
		}

		// ---------------------------------------------------------------- model

		/** Node kinds the palette offers; each compiles to an exact step. */
		const PALETTE = [
			{ kind: "click", color: "#0369a1" },
			{ kind: "input_text", color: "#0369a1" },
			{ kind: "keyboard", color: "#0369a1" },
			{ kind: "wait", color: "#475569" },
			{ kind: "condition", color: "#7c3aed" },
			{ kind: "screenshot", color: "#15803d" },
			{ kind: "replay_step", color: "#b45309" },
			{ kind: "run_command", color: "#b45309" }
		];

		/** Pipeline sentinels; they carry no step. */
		const SENTINEL_KINDS = ["start", "end"];

		/** Conditions the offline replayer can evaluate, and their value shape. */
		const CONDITION_KEYS = ["file_exists", "process_running", "process_gone", "shell_true", "window_appears"];

		/** Node kinds whose step may name the window it acts on. */
		const TARGETABLE = { click: true, input_text: true, keyboard: true };

		/** Field names this editor owns per step tool; anything else is preserved verbatim. */
		const OWNED_FIELDS = {
			click: ["tool", "coords", "button", "double", "target_window"],
			type_text: ["tool", "text_input", "target_window"],
			hotkey: ["tool", "keys", "repeat", "target_window"],
			"ext.delay": ["tool", "seconds"],
			"ext.screenshot": ["tool", "path"],
			run_shell: ["tool", "command", "shell", "timeout_s"],
			"ctrl.if": ["tool", "cond", "then", "else", "timeout_s"]
		};

		const NODE_W = 168;
		const NODE_H = 44;
		const COLUMN_X = 26;
		const ROW_TOP = 20;
		const ROW_GAP = 76;

		/** A refusal the canvas shows verbatim instead of guessing. */
		function FlowError(message, nodeId) {
			const error = new Error(message);
			error.name = "FlowError";
			error.nodeId = nodeId || "";
			return error;
		}

		function isObject(value) {
			return Boolean(value) && typeof value === "object" && !Array.isArray(value);
		}

		function numberOr(value, fallback) {
			const parsed = typeof value === "number" ? value : Number(value);
			return Number.isFinite(parsed) ? parsed : fallback;
		}

		function intOr(value, fallback) {
			return Math.round(numberOr(value, fallback));
		}

		function colorOf(kind) {
			for (let i = 0; i < PALETTE.length; i += 1) {
				if (PALETTE[i].kind === kind) return PALETTE[i].color;
			}
			if (kind === "start") return "#6366f1";
			if (kind === "end") return "#ef4444";
			return "#475569";
		}

		function labelOf(kind) {
			return translate("action." + kind);
		}

		/** Split "ctrl+s" / ["ctrl","s"] into the step's keys array. */
		function parseKeys(value) {
			if (Array.isArray(value)) {
				return value.map((item) => String(item).trim()).filter(Boolean);
			}
			return String(value == null ? "" : value)
				.split(/[+,]/)
				.map((item) => item.trim())
				.filter(Boolean);
		}

		function clone(value) {
			return JSON.parse(JSON.stringify(value));
		}

		/** Keys of `value` that are not in `owned`. */
		function extraFields(value, owned) {
			const out = {};
			const keys = Object.keys(value || {});
			for (let i = 0; i < keys.length; i += 1) {
				if (owned.indexOf(keys[i]) === -1) out[keys[i]] = value[keys[i]];
			}
			return out;
		}

		/** The window title a step names, or "" when it names none this editor owns. */
		function windowOfStep(step) {
			const hint = step.target_window;
			if (!isObject(hint)) return "";
			if (Object.keys(hint).length !== 1 || typeof hint.title !== "string") return "";
			return hint.title;
		}

		// --------------------------------------------------------------- codec

		/**
		 * Compile one node into its WPS step, or null when the node cannot be
		 * compiled exactly.
		 * @param node - canvas node.
		 * @returns the step object, or null.
		 */
		function stepForNode(node) {
			const params = node.params || {};
			let step = null;
			if (node.kind === "click") {
				step = { tool: "click", coords: [intOr(params.x, 0), intOr(params.y, 0)] };
				if (params.double === true) step.double = true;
				if (params.button && params.button !== "left") step.button = String(params.button);
			} else if (node.kind === "input_text") {
				step = { tool: "type_text", text_input: String(params.text == null ? "" : params.text) };
			} else if (node.kind === "keyboard") {
				const keys = parseKeys(params.keys);
				if (!keys.length) return null;
				step = { tool: "hotkey", keys: keys };
				const repeat = intOr(params.repeat, 1);
				if (repeat > 1) step.repeat = repeat;
			} else if (node.kind === "wait") {
				step = { tool: "ext.delay", seconds: Math.max(0, numberOr(params.seconds, 0)) };
			} else if (node.kind === "screenshot") {
				step = { tool: "ext.screenshot" };
				if (params.path) step.path = String(params.path);
			} else if (node.kind === "run_command") {
				const command = String(params.command == null ? "" : params.command);
				if (!command.trim()) return null;
				step = { tool: "run_shell", command: command };
				if (params.shell) step.shell = String(params.shell);
				const timeout = numberOr(params.timeout_s, 0);
				if (timeout > 0) step.timeout_s = timeout;
			} else if (node.kind === "replay_step") {
				if (!isObject(params.step)) return null;
				step = clone(params.step);
			} else {
				return null;
			}
			if (TARGETABLE[node.kind]) {
				const title = String(params.window == null ? "" : params.window).trim();
				if (title) step.target_window = { title: title };
			}
			// Recorded fields the panel does not own are written back unchanged,
			// so editing a coordinate never drops a CV reference frame.
			if (isObject(params.extra)) {
				const extraKeys = Object.keys(params.extra);
				for (let i = 0; i < extraKeys.length; i += 1) {
					if (!(extraKeys[i] in step)) step[extraKeys[i]] = clone(params.extra[extraKeys[i]]);
				}
			}
			return step;
		}

		/** Build the `cond` object of a condition node, or null when it has none. */
		function conditionOf(params) {
			if (params.key) return { [params.key]: String(params.value == null ? "" : params.value) };
			if (isObject(params.cond) && Object.keys(params.cond).length) return clone(params.cond);
			return null;
		}

		/**
		 * Compile a node list into a step list plus an index-to-node map.
		 *
		 * A condition node owns the rest of the pipeline: it compiles to one
		 * `ctrl.if` whose `then` block is everything after it, which is exactly
		 * what the offline replayer does with a flat `ctrl.if` - run the block when
		 * the condition holds, otherwise fall through to the end of the script.
		 * @param nodes - canvas nodes.
		 * @returns `{ steps, owners }`, owners keyed by the replayer's step index.
		 */
		function compileNodes(nodes) {
			const owners = {};
			const middle = (Array.isArray(nodes) ? nodes : []).filter((node) => SENTINEL_KINDS.indexOf(node.kind) === -1);
			const steps = compileList(middle, "", owners);
			return { steps: steps, owners: owners };
		}

		function compileList(list, prefix, owners) {
			const steps = [];
			for (let i = 0; i < list.length; i += 1) {
				const node = list[i];
				const index = prefix + (steps.length + 1);
				if (node.kind === "condition") {
					const cond = conditionOf(node.params || {});
					if (!cond) throw FlowError("条件节点缺少可编译的条件", node.id);
					const nested = compileList(list.slice(i + 1), index + ".", owners);
					const step = { tool: "ctrl.if", cond: cond, then: nested };
					const timeout = numberOr((node.params || {}).timeout_s, 0);
					if (timeout > 0) step.timeout_s = timeout;
					if (isObject((node.params || {}).extra)) {
						const extraKeys = Object.keys(node.params.extra);
						for (let k = 0; k < extraKeys.length; k += 1) {
							if (!(extraKeys[k] in step)) step[extraKeys[k]] = clone(node.params.extra[extraKeys[k]]);
						}
					}
					owners[index] = { nodeId: node.id, kind: "ctrl" };
					steps.push(step);
					return steps;
				}
				const step = stepForNode(node);
				if (!step) throw FlowError("节点「" + labelOf(node.kind) + "」缺少必填参数，无法编译", node.id);
				owners[index] = { nodeId: node.id, kind: "step" };
				steps.push(step);
			}
			return steps;
		}

		/** Wrap an unrecognized step so it is rendered and round-tripped verbatim. */
		function fragmentNode(step) {
			return { kind: "replay_step", params: { step: clone(step) } };
		}

		/**
		 * Map one step onto the editor's own node, or onto an opaque fragment.
		 *
		 * A step keeps its editable node only when every field it carries is one
		 * this editor owns; anything else is preserved in `params.extra` so the
		 * document survives a round trip unchanged.
		 * @param step - one WPS step.
		 * @returns a node descriptor without identity or position.
		 */
		function nodeForStep(step) {
			const tool = step.tool;
			const owned = OWNED_FIELDS[tool];
			if (!owned) return fragmentNode(step);
			if (tool === "ctrl.if") return null;
			const extra = extraFields(step, owned);
			const named = windowOfStep(step);
			const window = named;
			if (named) delete extra.target_window;
			else if ("target_window" in step) extra.target_window = step.target_window;
			if (tool === "click") {
				if (!Array.isArray(step.coords)) return fragmentNode(step);
				return {
					kind: "click",
					params: {
						x: intOr(step.coords[0], 0),
						y: intOr(step.coords[1], 0),
						button: step.button ? String(step.button) : "left",
						double: step.double === true,
						window: window,
						extra: extra
					}
				};
			}
			if (tool === "type_text") {
				return { kind: "input_text", params: { text: String(step.text_input == null ? "" : step.text_input), window: window, extra: extra } };
			}
			if (tool === "hotkey") {
				if (!Array.isArray(step.keys)) return fragmentNode(step);
				return {
					kind: "keyboard",
					params: {
						keys: step.keys.map(String).join("+"),
						repeat: intOr(step.repeat, 1),
						window: window,
						extra: extra
					}
				};
			}
			if (tool === "ext.delay") {
				return { kind: "wait", params: { seconds: numberOr(step.seconds, 0), extra: extra } };
			}
			if (tool === "ext.screenshot") {
				return { kind: "screenshot", params: { path: step.path == null ? "" : String(step.path), extra: extra } };
			}
			if (tool === "run_shell") {
				return {
					kind: "run_command",
					params: {
						command: String(step.command == null ? "" : step.command),
						shell: step.shell == null ? "" : String(step.shell),
						timeout_s: numberOr(step.timeout_s, 0),
						extra: extra
					}
				};
			}
			return fragmentNode(step);
		}

		/**
		 * A condition step is editable only when its cond is one key of a known shape.
		 *
		 * An absent `timeout_s` stays absent: the replayer's own default is what an
		 * omitted value means, and writing it back would edit a recorded document
		 * that nobody touched.
		 * @param step - one `ctrl.if` step.
		 * @returns condition node parameters.
		 */
		function conditionParams(step) {
			const cond = isObject(step.cond) ? step.cond : {};
			const keys = Object.keys(cond);
			const extra = extraFields(step, OWNED_FIELDS["ctrl.if"]);
			const params = {
				timeout_s: step.timeout_s === undefined ? "" : numberOr(step.timeout_s, 6),
				extra: extra
			};
			const simple = keys.length === 1
				&& CONDITION_KEYS.indexOf(keys[0]) !== -1
				&& typeof cond[keys[0]] !== "object";
			if (simple) {
				params.key = keys[0];
				params.value = String(cond[keys[0]]);
			} else {
				params.key = "";
				params.value = "";
				params.cond = cond;
			}
			return params;
		}

		/**
		 * Expand a step list into ordered node descriptors.
		 *
		 * A `ctrl.if` becomes an editable condition node only when it is the last
		 * step of its list and carries no else-block, because that is the one shape
		 * a linear pipeline reproduces exactly: everything after it becomes its
		 * `then` block. Any other control-flow step stays an opaque fragment.
		 * @param steps - one WPS step list.
		 * @returns node descriptors in execution order.
		 */
		function explodeSteps(steps) {
			const out = [];
			for (let i = 0; i < steps.length; i += 1) {
				const step = steps[i];
				if (!isObject(step)) throw FlowError("步骤 " + (i + 1) + " 不是对象");
				const trailing = i === steps.length - 1;
				const hasElse = Array.isArray(step.else) && step.else.length > 0;
				if (step.tool === "ctrl.if" && trailing && !hasElse) {
					out.push({ kind: "condition", params: conditionParams(step) });
					const nested = explodeSteps(Array.isArray(step.then) ? step.then : []);
					for (let n = 0; n < nested.length; n += 1) out.push(nested[n]);
					continue;
				}
				const mapped = nodeForStep(step);
				out.push(mapped || fragmentNode(step));
			}
			return out;
		}

		/** Give expanded descriptors identity and grid positions. */
		function layoutNodes(descriptors) {
			const nodes = [{ id: "start", kind: "start", x: COLUMN_X, y: ROW_TOP, params: {} }];
			for (let i = 0; i < descriptors.length; i += 1) {
				nodes.push({
					id: "n" + (i + 1),
					kind: descriptors[i].kind,
					params: descriptors[i].params || {},
					x: COLUMN_X,
					y: ROW_TOP + (i + 1) * ROW_GAP
				});
			}
			nodes.push({
				id: "end",
				kind: "end",
				x: COLUMN_X,
				y: ROW_TOP + (nodes.length) * ROW_GAP,
				params: {}
			});
			return nodes;
		}

		/** Re-snap every node onto the pipeline grid, in array order. */
		function snap(nodes) {
			const out = [];
			for (let i = 0; i < nodes.length; i += 1) {
				out.push(Object.assign({}, nodes[i], { x: COLUMN_X, y: ROW_TOP + i * ROW_GAP }));
			}
			return out;
		}

		/** Default parameters for a freshly added node. */
		function defaultParams(kind) {
			if (kind === "click") return { x: 960, y: 540, button: "left", double: false, window: "" };
			if (kind === "input_text") return { text: "", window: "" };
			if (kind === "keyboard") return { keys: "enter", repeat: 1, window: "" };
			if (kind === "wait") return { seconds: 1 };
			if (kind === "condition") return { key: "file_exists", value: "", timeout_s: 6 };
			if (kind === "screenshot") return { path: "" };
			if (kind === "run_command") return { command: "", shell: "", timeout_s: 0 };
			if (kind === "replay_step") return { step: { tool: "ext.delay", seconds: 0 } };
			return {};
		}

		/** Build one Automation IR document from the canvas. */
		function toDocument(input) {
			const nodes = input.nodes || [];
			const compiled = compileNodes(nodes);
			const document = {
				format: "automation-ir/1",
				id: input.id || "air-dsh-" + Math.random().toString(36).slice(2, 14),
				name: input.name || translate("status.untitled"),
				created: intOr(input.created, 0) || Math.floor(Date.now() / 1000),
				source: {
					kind: "wps",
					format: "wps/2",
					source: "dsh-flow-workbench",
					source_run: "",
					task: input.task || ""
				},
				conditions: input.conditions && isObject(input.conditions) ? clone(input.conditions) : {},
				evidence: input.evidence && isObject(input.evidence) ? clone(input.evidence) : { required: [] },
				steps: compiled.steps,
				variables: input.variables && isObject(input.variables) ? clone(input.variables) : {},
				runtime: { kind: "wps_replay" }
			};
			return document;
		}

		/**
		 * Load a document onto the canvas.
		 * @param document - an `automation-ir/1` or legacy `wps/*` document.
		 * @returns canvas state, or throws when the document is not a linear flow.
		 */
		function fromDocument(document) {
			if (!isObject(document)) throw FlowError("文档不是 JSON 对象");
			const format = document.format;
			let steps = null;
			let runtimeKind = "";
			if (format === "automation-ir/1") {
				runtimeKind = isObject(document.runtime) && document.runtime.kind
					? String(document.runtime.kind)
					: (isObject(document.source) && document.source.kind === "wps" ? "wps_replay" : "");
				if (runtimeKind !== "wps_replay") {
					throw FlowError(
						"流程图工作台只编辑 wps_replay 线性流程；这份文档的 runtime.kind 是 "
						+ (runtimeKind || "（未声明）")
					);
				}
				steps = document.steps;
			} else if (format === "wps/1" || format === "wps/2") {
				steps = document.steps;
			} else {
				throw FlowError("不认识的文档格式：" + String(format));
			}
			if (!Array.isArray(steps)) throw FlowError("文档 steps 不是数组");
			return {
				id: typeof document.id === "string" ? document.id : "",
				name: typeof document.name === "string" ? document.name : "",
				created: numberOr(document.created, 0),
				task: isObject(document.source) && typeof document.source.task === "string" ? document.source.task : "",
				conditions: isObject(document.conditions) ? document.conditions : {},
				evidence: isObject(document.evidence) ? document.evidence : { required: [] },
				variables: isObject(document.variables) ? document.variables : {},
				nodes: layoutNodes(explodeSteps(steps))
			};
		}

		/** Every codec entry point, exposed so the bundle can be verified offline. */
		const flowCodec = {
			PALETTE: PALETTE,
			CONDITION_KEYS: CONDITION_KEYS,
			SENTINEL_KINDS: SENTINEL_KINDS,
			compileNodes: compileNodes,
			explodeSteps: explodeSteps,
			layoutNodes: layoutNodes,
			defaultParams: defaultParams,
			toDocument: toDocument,
			fromDocument: fromDocument,
			parseKeys: parseKeys,
			stepForNode: stepForNode
		};

		// ------------------------------------------------------------------- io

		/** One JSON call against the plugin's own host bridge. */
		async function bridge(method, suffix, body) {
			const response = await fetch(BRIDGE + suffix, {
				method: method,
				headers: Object.assign(
					{ accept: "application/json" },
					body === undefined ? {} : { "content-type": "application/json" }
				),
				body: body === undefined ? undefined : JSON.stringify(body)
			});
			const text = await response.text();
			let parsed = null;
			try {
				parsed = text ? JSON.parse(text) : null;
			} catch (error) {
				parsed = { error: text };
			}
			if (!response.ok) {
				const detail = parsed && (parsed.error || parsed.detail);
				throw new Error(typeof detail === "string" && detail ? detail : "HTTP " + response.status);
			}
			return parsed;
		}

		/** The default canvas: a pipeline that runs without touching the desktop. */
		function defaultNodes() {
			return layoutNodes([
				{ kind: "wait", params: { seconds: 1 } },
				{ kind: "screenshot", params: { path: "" } }
			]);
		}

		// ------------------------------------------------------------------- ui

		/** Panel title seat. */
		function LingdongFlowTitle() {
			return jsx("span", { children: translate("tab.title") });
		}

		/** Vertical centre of a node in flow coordinates. */
		function centerOf(node) {
			return { x: node.x + NODE_W / 2, y: node.y + NODE_H / 2 };
		}

		/** A top-to-bottom connector between two stacked nodes. */
		function edgePath(from, to) {
			const ax = from.x + NODE_W / 2;
			const ay = from.y + NODE_H;
			const bx = to.x + NODE_W / 2;
			const by = to.y;
			const mid = Math.max(18, (by - ay) / 2);
			return "M " + ax + " " + ay + " C " + ax + " " + (ay + mid) + ", " + bx + " " + (by - mid) + ", " + bx + " " + by;
		}

		/** A one-line summary of the run state for the status bar. */
		function runSummary(run) {
			if (!run || run.state === "idle") return translate("run.idle");
			if (run.state === "done") return translate("run.done") + " · " + run.id;
			if (run.state === "failed") return translate("run.failed") + (run.error ? "：" + run.error : "");
			if (run.state === "stopped") return translate("run.stopped");
			return translate("run." + run.state) + (run.id ? " · " + run.id : "");
		}

		/**
		 * The flow workbench pane: library, palette, canvas, inspector, status bar.
		 * Kept dependency-free so it can be server-rendered for verification and
		 * then mounted unchanged in the browser.
		 */
		function LingdongFlowPane() {
			const nodesState = react.useState(defaultNodes);
			const nodes = nodesState[0];
			const setNodes = nodesState[1];
			const selectedState = react.useState("");
			const selectedId = selectedState[0];
			const setSelectedId = selectedState[1];
			const viewState = react.useState({ scale: 1 });
			const view = viewState[0];
			const setView = viewState[1];
			const dragState = react.useState(null);
			const drag = dragState[0];
			const setDrag = dragState[1];
			const docState = react.useState(() => ({ id: "", name: "", created: 0, task: "", conditions: {}, evidence: { required: [] }, variables: {}, file: "" }));
			const doc = docState[0];
			const setDoc = docState[1];
			const dirtyState = react.useState(false);
			const dirty = dirtyState[0];
			const setDirty = dirtyState[1];
			const libraryState = react.useState({ phase: "idle", flows: [], error: "" });
			const library = libraryState[0];
			const setLibrary = libraryState[1];
			const noticeState = react.useState(null);
			const notice = noticeState[0];
			const setNotice = noticeState[1];
			const runState = react.useState({ state: "idle", id: "", error: "", nodes: {} });
			const run = runState[0];
			const setRun = runState[1];
			const pollRef = react.useRef(null);
			const runRef = react.useRef(run);
			runRef.current = run;

			function t(key) {
				return translate(key);
			}

			/** Load the flow library from the host bridge. */
			async function refreshLibrary() {
				setLibrary((previous) => Object.assign({}, previous, { phase: "loading", error: "" }));
				try {
					const answer = await bridge("GET", "/flows");
					setLibrary({ phase: "ready", flows: Array.isArray(answer.flows) ? answer.flows : [], error: "" });
				} catch (error) {
					setLibrary({ phase: "error", flows: [], error: String(error.message || error) });
				}
			}

			react.useEffect(() => {
				refreshLibrary();
				return () => {
					if (pollRef.current !== null) clearTimeout(pollRef.current);
				};
			}, []);

			function selectedNode() {
				for (let i = 0; i < nodes.length; i += 1) {
					if (nodes[i].id === selectedId) return nodes[i];
				}
				return null;
			}

			/** Replace one node's parameters and mark the canvas dirty. */
			function patchSelected(patch) {
				const next = [];
				for (let i = 0; i < nodes.length; i += 1) {
					const node = nodes[i];
					if (node.id !== selectedId) {
						next.push(node);
						continue;
					}
					next.push(Object.assign({}, node, { params: Object.assign({}, node.params, patch) }));
				}
				setNodes(next);
				setDirty(true);
			}

			/** Insert a node after the selection (or before `end`), then select it. */
			function addNode(kind) {
				const fresh = { id: "n" + Date.now().toString(36) + Math.random().toString(36).slice(2, 5), kind: kind, params: defaultParams(kind), x: COLUMN_X, y: ROW_TOP };
				let at = nodes.length - 1;
				for (let i = 0; i < nodes.length; i += 1) {
					if (nodes[i].id === selectedId) at = i + 1;
				}
				if (at >= nodes.length) at = nodes.length - 1;
				const next = nodes.slice(0, at).concat([fresh], nodes.slice(at));
				setNodes(snap(next));
				setSelectedId(fresh.id);
				setDirty(true);
			}

			function deleteSelected() {
				if (!selectedId) return;
				const target = selectedNode();
				if (!target || SENTINEL_KINDS.indexOf(target.kind) !== -1) return;
				const next = nodes.filter((node) => node.id !== selectedId);
				setNodes(snap(next));
				setSelectedId("");
				setDirty(true);
			}

			/** Move one executable node one slot up or down the pipeline. */
			function moveSelected(delta) {
				const target = selectedNode();
				if (!target || SENTINEL_KINDS.indexOf(target.kind) !== -1) return;
				const at = nodes.indexOf(target);
				const to = at + delta;
				if (to < 1 || to > nodes.length - 2) return;
				const next = nodes.slice();
				next.splice(at, 1);
				next.splice(to, 0, target);
				setNodes(snap(next));
				setDirty(true);
			}

			function renameSelected(value) {
				const next = nodes.map((node) => (node.id === selectedId ? Object.assign({}, node, { label: value }) : node));
				setNodes(next);
				setDirty(true);
			}

			function zoomBy(factor) {
				setView({ scale: Math.min(2.5, Math.max(0.4, view.scale * factor)) });
			}

			function startDrag(event, node) {
				if (event && event.stopPropagation) event.stopPropagation();
				setSelectedId(node.id);
				if (SENTINEL_KINDS.indexOf(node.kind) !== -1) return;
				setDrag({
					id: node.id,
					index: nodes.indexOf(node),
					originY: node.y,
					fromY: event && typeof event.clientY === "number" ? event.clientY : 0
				});
			}

			function moveDrag(event) {
				if (!drag || !event || typeof event.clientY !== "number") return;
				const dy = (event.clientY - drag.fromY) / view.scale;
				setNodes(nodes.map((node) => (node.id === drag.id ? Object.assign({}, node, { y: drag.originY + dy }) : node)));
			}

			/** Dropping a node reorders the pipeline: vertical position is the order. */
			function endDrag() {
				if (!drag) return;
				const moved = nodes.filter((node) => node.id === drag.id)[0];
				setDrag(null);
				if (!moved) return;
				const middle = nodes.filter((node) => SENTINEL_KINDS.indexOf(node.kind) === -1);
				const sorted = middle.slice().sort((a, b) => centerOf(a).y - centerOf(b).y);
				const changed = sorted.some((node, index) => node.id !== middle[index].id);
				if (!changed) {
					setNodes(snap(nodes));
					return;
				}
				setNodes(snap([nodes[0]].concat(sorted, [nodes[nodes.length - 1]])));
				setDirty(true);
			}

			/** Build the document the canvas currently represents. */
			function currentDocument() {
				return flowCodec.toDocument({
					id: doc.id,
					name: doc.name || doc.file.replace(/\.automation\.json$/, ""),
					created: doc.created,
					task: doc.task,
					conditions: doc.conditions,
					evidence: doc.evidence,
					variables: doc.variables,
					nodes: nodes
				});
			}

			function newFlow() {
				setNodes(defaultNodes());
				setDoc({ id: "", name: "", created: 0, task: "", conditions: {}, evidence: { required: [] }, variables: {}, file: "" });
				setSelectedId("");
				setDirty(false);
				setRun({ state: "idle", id: "", error: "", nodes: {} });
				setNotice({ tone: "info", text: t("notice.created") });
			}

			async function openFlow(entry) {
				try {
					const document = await bridge("GET", "/flows/" + encodeURIComponent(entry.file));
					const loaded = flowCodec.fromDocument(document);
					setNodes(loaded.nodes);
					setDoc({
						id: loaded.id,
						name: loaded.name,
						created: loaded.created,
						task: loaded.task,
						conditions: loaded.conditions,
						evidence: loaded.evidence,
						variables: loaded.variables,
						file: entry.file
					});
					setSelectedId("");
					setDirty(false);
					setRun({ state: "idle", id: "", error: "", nodes: {} });
					setNotice({ tone: "info", text: t("notice.opened") + "：" + entry.file });
				} catch (error) {
					setNotice({ tone: "error", text: t("notice.loadFailed") + "：" + String(error.message || error) });
				}
			}

			async function saveFlow() {
				const file = String(doc.file || "").trim();
				if (!file) {
					setNotice({ tone: "error", text: t("notice.noFile") });
					return;
				}
				try {
					const document = currentDocument();
					await bridge("PUT", "/flows/" + encodeURIComponent(file), document);
					setDoc((previous) => Object.assign({}, previous, { id: document.id, name: document.name, created: document.created }));
					setDirty(false);
					setNotice({ tone: "info", text: t("notice.saved") + "：" + file });
					refreshLibrary();
				} catch (error) {
					setNotice({ tone: "error", text: t("notice.saveFailed") + "：" + String(error.message || error) });
				}
			}

			/** Apply one polled replay job onto the canvas's per-node status. */
			function applyRunResult(job, owners) {
				const records = (job && job.result && Array.isArray(job.result.results)) ? job.result.results : [];
				const status = {};
				for (let i = 0; i < records.length; i += 1) {
					const record = records[i];
					const owner = owners[String(record.index)];
					if (!owner) continue;
					status[owner.nodeId] = {
						ok: record.ok === true,
						index: String(record.index),
						tool: String(record.tool || ""),
						error: record.error ? String(record.error) : ""
					};
				}
				const keys = Object.keys(owners);
				for (let i = 0; i < keys.length; i += 1) {
					const index = keys[i];
					const owner = owners[index];
					if (owner.kind !== "ctrl") continue;
					let taken = false;
					let childFailed = false;
					for (let j = 0; j < records.length; j += 1) {
						if (String(records[j].index).indexOf(index + ".") !== 0) continue;
						taken = true;
						if (records[j].ok !== true) childFailed = true;
					}
					if (!status[owner.nodeId]) {
						status[owner.nodeId] = { ok: !childFailed, index: index, tool: "ctrl.if", error: "", ctrl: true, taken: taken };
					}
				}
				return status;
			}

			function stopRun() {
				if (pollRef.current !== null) {
					clearTimeout(pollRef.current);
					pollRef.current = null;
				}
				setRun((previous) => Object.assign({}, previous, { state: "stopped" }));
			}

			/**
			 * Validate the canvas document with the runtime, then submit and poll it.
			 *
			 * The canvas never decides whether a flow is runnable: the runtime's own
			 * validator does, and its message is what the user sees.
			 */
			async function runFlow() {
				if (runRef.current.state === "running" || runRef.current.state === "submitting" || runRef.current.state === "validating") return;
				let document;
				let compiled;
				try {
					document = currentDocument();
					compiled = flowCodec.compileNodes(nodes);
				} catch (error) {
					setNotice({ tone: "error", text: t("notice.blocked") + "：" + String(error.message || error) });
					setRun({ state: "failed", id: "", error: String(error.message || error), nodes: {} });
					return;
				}
				setRun({ state: "validating", id: "", error: "", nodes: {} });
				try {
					const validation = await bridge("POST", "/validate", document);
					if (!validation || validation.valid !== true) {
						const errors = (validation && validation.errors) || [];
						setRun({ state: "failed", id: "", error: errors.join("；") || t("run.invalid"), nodes: {} });
						setNotice({ tone: "error", text: t("run.invalid") + "：" + (errors.join("；") || "") });
						return;
					}
				} catch (error) {
					setRun({ state: "failed", id: "", error: String(error.message || error), nodes: {} });
					setNotice({ tone: "error", text: t("notice.runFailed") + "：" + String(error.message || error) });
					return;
				}
				setRun({ state: "submitting", id: "", error: "", nodes: {} });
				let started;
				try {
					started = await bridge("POST", "/run", { automation: document });
				} catch (error) {
					setRun({ state: "failed", id: "", error: String(error.message || error), nodes: {} });
					setNotice({ tone: "error", text: t("notice.runFailed") + "：" + String(error.message || error) });
					return;
				}
				const kind = started.kind === "arp" ? "arp" : "replay";
				setRun({ state: "running", id: started.id, error: "", nodes: {} });

				/** Poll until the runtime reports a terminal state. */
				async function poll() {
					pollRef.current = null;
					let answer;
					try {
						answer = await bridge("GET", "/runs/" + encodeURIComponent(started.id) + "?kind=" + kind);
					} catch (error) {
						setRun({ state: "failed", id: started.id, error: String(error.message || error), nodes: {} });
						return;
					}
					const job = kind === "replay" ? answer.job : answer.run;
					const status = String((job && job.status) || answer.status || "");
					const terminal = ["done", "failed", "stopped", "canceled", "cancelled"].indexOf(status) !== -1;
					if (terminal) {
						const failed = status !== "done" || (job && job.ok === false);
						setRun({
							state: failed ? "failed" : "done",
							id: started.id,
							error: failed ? String((job && job.error) || "") : "",
							nodes: applyRunResult(job, compiled.owners)
						});
						return;
					}
					setRun((previous) => Object.assign({}, previous, { state: "running", id: started.id }));
					pollRef.current = setTimeout(poll, 400);
				}
				poll();
			}

			const executable = nodes.filter((node) => SENTINEL_KINDS.indexOf(node.kind) === -1);
			const byId = {};
			for (let i = 0; i < nodes.length; i += 1) byId[nodes[i].id] = nodes[i];

			const edgeShapes = [];
			for (let i = 0; i + 1 < nodes.length; i += 1) {
				edgeShapes.push(jsx("path", {
					key: "edge-" + nodes[i].id,
					d: edgePath(nodes[i], nodes[i + 1]),
					fill: "none",
					stroke: "#94a3b8",
					strokeWidth: 2
				}));
			}

			const nodeBoxes = [];
			for (let i = 0; i < nodes.length; i += 1) {
				const node = nodes[i];
				const isSelected = node.id === selectedId;
				const outcome = run.nodes[node.id];
				const border = isSelected ? "2px solid #2563eb" : "1px solid #cbd5e1";
				let badge = null;
				if (outcome) {
					badge = jsx("span", {
						"data-lingdong-outcome": outcome.ok ? "ok" : "fail",
						style: { marginLeft: "auto", fontSize: "10px", color: outcome.ok ? "#15803d" : "#b91c1c" },
						children: outcome.ok ? "✓" : "✕"
					});
				}
				nodeBoxes.push(jsxs("div", {
					key: node.id,
					"data-lingdong-node": node.kind,
					"data-lingdong-node-id": node.id,
					onPointerDown: (event) => startDrag(event, node),
					style: {
						position: "absolute",
						left: node.x + "px",
						top: node.y + "px",
						width: NODE_W + "px",
						height: NODE_H + "px",
						borderRadius: "8px",
						border: border,
						borderLeft: "4px solid " + colorOf(node.kind),
						background: "#ffffff",
						display: "flex",
						alignItems: "center",
						padding: "0 10px",
						fontSize: "12px",
						cursor: SENTINEL_KINDS.indexOf(node.kind) === -1 ? "grab" : "default",
						boxSizing: "border-box",
						userSelect: "none"
					},
					children: [
						jsx("span", { key: "l", children: node.label && node.label !== labelOf(node.kind) ? node.label : labelOf(node.kind) }),
						badge
					]
				}));
			}

			const paletteButtons = [];
			for (let i = 0; i < PALETTE.length; i += 1) {
				const entry = PALETTE[i];
				paletteButtons.push(jsx("button", {
					key: entry.kind,
					type: "button",
					onClick: () => addNode(entry.kind),
					"data-lingdong-palette": entry.kind,
					style: {
						display: "block",
						width: "100%",
						marginBottom: "4px",
						padding: "4px 8px",
						textAlign: "left",
						fontSize: "11px",
						border: "1px solid #e2e8f0",
						borderLeft: "3px solid " + entry.color,
						borderRadius: "4px",
						background: "#ffffff",
						cursor: "pointer"
					},
					children: labelOf(entry.kind)
				}));
			}

			const libraryRows = [];
			for (let i = 0; i < library.flows.length; i += 1) {
				const entry = library.flows[i];
				const active = entry.file === doc.file;
				libraryRows.push(jsxs("button", {
					key: entry.file,
					type: "button",
					onClick: () => openFlow(entry),
					"data-lingdong-flow-file": entry.file,
					title: entry.validation_error || entry.file,
					style: {
						display: "block",
						width: "100%",
						marginBottom: "3px",
						padding: "3px 6px",
						textAlign: "left",
						fontSize: "11px",
						border: active ? "1px solid #2563eb" : "1px solid #e2e8f0",
						borderRadius: "4px",
						background: active ? "#eff6ff" : "#ffffff",
						color: entry.valid === false ? "#b91c1c" : "#0f172a",
						cursor: "pointer",
						overflow: "hidden",
						textOverflow: "ellipsis",
						whiteSpace: "nowrap"
					},
					children: [
						jsx("span", { key: "n", children: entry.name || entry.file }),
						jsx("span", { key: "c", style: { opacity: 0.55, marginLeft: "6px" }, children: String(entry.steps) })
					]
				}));
			}

			const current = selectedNode();
			const inspector = [];
			inspector.push(jsxs("label", { key: "file", style: { display: "block", marginBottom: "6px" }, children: [
				t("inspector.file"),
				jsx("input", {
					value: doc.file,
					"data-lingdong-field": "file",
					placeholder: "my-flow.automation.json",
					onChange: (event) => {
						setDoc((previous) => Object.assign({}, previous, { file: event.target.value }));
						setDirty(true);
					},
					style: { width: "100%", marginTop: "2px", fontFamily: "monospace", fontSize: "11px" }
				})
			] }));
			inspector.push(jsx("div", { key: "fileHint", style: { opacity: 0.6, marginBottom: "6px" }, children: t("inspector.fileHint") }));
			if (current) {
				const params = current.params || {};
				inspector.push(jsx("div", { key: "kind", children: t("inspector.kind") + "：" + labelOf(current.kind) }));
				if (SENTINEL_KINDS.indexOf(current.kind) === -1) {
					inspector.push(jsxs("label", { key: "label", style: { display: "block", marginTop: "4px" }, children: [
						t("inspector.label"),
						jsx("input", {
							value: current.label == null ? "" : current.label,
							onChange: (event) => renameSelected(event.target.value),
							style: { width: "100%", marginTop: "2px" }
						})
					] }));
					if (current.kind === "click") {
						inspector.push(field("x", "number", params.x, (value) => patchSelected({ x: intOr(value, 0) })));
						inspector.push(field("y", "number", params.y, (value) => patchSelected({ y: intOr(value, 0) })));
						inspector.push(select("button", ["left", "right", "middle"], params.button || "left", (value) => patchSelected({ button: value })));
						inspector.push(checkbox("double", params.double === true, (value) => patchSelected({ double: value })));
					} else if (current.kind === "input_text") {
						inspector.push(field("text", "textarea", params.text, (value) => patchSelected({ text: value })));
					} else if (current.kind === "keyboard") {
						inspector.push(field("keys", "text", params.keys, (value) => patchSelected({ keys: value })));
						inspector.push(hint("keysHint"));
					} else if (current.kind === "wait") {
						inspector.push(field("seconds", "number", params.seconds, (value) => patchSelected({ seconds: Math.max(0, numberOr(value, 0)) })));
					} else if (current.kind === "condition") {
						if (params.key) {
							inspector.push(select("conditionKey", CONDITION_KEYS, params.key, (value) => patchSelected({ key: value })));
							inspector.push(field("conditionValue", "text", params.value, (value) => patchSelected({ value: value })));
						} else {
							inspector.push(hint("conditionOpaque"));
						}
						inspector.push(field("timeout", "number", params.timeout_s, (value) => patchSelected({ timeout_s: Math.max(0, numberOr(value, 0)) })));
					} else if (current.kind === "screenshot") {
						inspector.push(field("path", "text", params.path, (value) => patchSelected({ path: value })));
						inspector.push(hint("pathHint"));
					} else if (current.kind === "run_command") {
						inspector.push(field("command", "textarea", params.command, (value) => patchSelected({ command: value })));
						inspector.push(field("shell", "text", params.shell, (value) => patchSelected({ shell: value })));
						inspector.push(field("timeout", "number", params.timeout_s, (value) => patchSelected({ timeout_s: Math.max(0, numberOr(value, 0)) })));
					} else if (current.kind === "replay_step") {
						inspector.push(jsxs("label", { key: "step", style: { display: "block", marginTop: "4px" }, children: [
							t("inspector.stepJson"),
							jsx("textarea", {
								value: JSON.stringify(params.step || {}, null, 1),
								rows: 6,
								onChange: (event) => {
									try {
										patchSelected({ step: JSON.parse(event.target.value) });
									} catch (error) {
										// Keep the last valid step while the text is mid-edit.
									}
								},
								style: { width: "100%", fontFamily: "monospace", fontSize: "11px" }
							})
						] }));
					}
					if (TARGETABLE[current.kind]) {
						inspector.push(field("window", "text", params.window, (value) => patchSelected({ window: value })));
						inspector.push(hint("windowHint"));
					}
					if (isObject(params.extra) && Object.keys(params.extra).length) {
						inspector.push(jsx("div", {
							key: "extra",
							"data-lingdong-preserved": Object.keys(params.extra).join(","),
							style: { marginTop: "6px", padding: "4px", background: "#fef9c3", borderRadius: "4px" },
							children: [
								jsx("div", { key: "t", children: t("inspector.preserved") + "：" + Object.keys(params.extra).join("、") }),
								jsx("div", { key: "h", style: { opacity: 0.7 }, children: t("inspector.preservedHint") })
							]
						}));
					}
					inspector.push(jsxs("div", { key: "move", style: { marginTop: "6px", display: "flex", gap: "4px" }, children: [
						jsx("button", { type: "button", onClick: () => moveSelected(-1), children: t("inspector.moveUp") }),
						jsx("button", { type: "button", onClick: () => moveSelected(1), children: t("inspector.moveDown") })
					] }));
					inspector.push(jsx("div", { key: "pos", style: { opacity: 0.6, marginTop: "4px" }, children: t("inspector.position") + "：" + (executable.indexOf(current) + 1) + " / " + executable.length }));
				}
			} else {
				inspector.push(jsx("span", { key: "empty", style: { opacity: 0.6 }, children: t("inspector.empty") }));
			}

			/** One labelled input row. */
			function field(name, type, value, onChange) {
				const label = t("inspector." + name);
				const common = {
					key: name,
					style: { width: "100%", marginTop: "2px" },
					"data-lingdong-field": name
				};
				if (type === "textarea") {
					return jsxs("label", { style: { display: "block", marginTop: "4px" }, children: [
						label,
						jsx("textarea", Object.assign({}, common, { value: value == null ? "" : String(value), rows: 3, onChange: (event) => onChange(event.target.value) }))
					] });
				}
				if (type === "number") {
					return jsxs("label", { style: { display: "block", marginTop: "4px" }, children: [
						label,
						jsx("input", Object.assign({}, common, { type: "number", value: value == null ? "" : String(value), onChange: (event) => onChange(event.target.value) }))
					] });
				}
				return jsxs("label", { style: { display: "block", marginTop: "4px" }, children: [
					label,
					jsx("input", Object.assign({}, common, { value: value == null ? "" : String(value), onChange: (event) => onChange(event.target.value) }))
				] });
			}

			/** One labelled select row. */
			function select(name, options, value, onChange) {
				const children = [];
				for (let i = 0; i < options.length; i += 1) {
					children.push(jsx("option", { key: options[i], value: options[i], children: name === "conditionKey" ? options[i] : options[i] }));
				}
				return jsxs("label", { style: { display: "block", marginTop: "4px" }, children: [
					t("inspector." + name),
					jsx("select", {
						value: value,
						"data-lingdong-field": name,
						onChange: (event) => onChange(event.target.value),
						style: { width: "100%", marginTop: "2px" },
						children: children
					})
				] });
			}

			/** One checkbox row. */
			function checkbox(name, value, onChange) {
				return jsxs("label", { style: { display: "block", marginTop: "4px" }, children: [
					jsx("input", {
						type: "checkbox",
						checked: value === true,
						"data-lingdong-field": name,
						onChange: (event) => onChange(event.target.checked)
					}),
					" " + t("inspector." + name)
				] });
			}

			/** One hint line. */
			function hint(name) {
				return jsx("div", { key: name, style: { opacity: 0.6, marginTop: "2px" }, children: t("inspector." + name) });
			}

			const title = doc.name || (doc.file ? doc.file : t("status.untitled"));
			const phase = run.state;
			const busy = phase === "validating" || phase === "submitting" || phase === "running";

			return jsxs("div", {
				"data-lingdong-flow-workbench": "1",
				style: { display: "flex", flexDirection: "column", height: "100%", fontSize: "12px", background: "#f8fafc" },
				children: [
					jsxs("div", {
						key: "toolbar",
						style: { display: "flex", flexWrap: "wrap", gap: "4px", padding: "6px 8px", borderBottom: "1px solid #e2e8f0", background: "#ffffff" },
						children: [
							jsx("button", { key: "new", type: "button", onClick: newFlow, children: t("toolbar.new") }),
							jsx("button", { key: "save", type: "button", onClick: saveFlow, "data-lingdong-save": "1", children: doc.file ? t("toolbar.save") : t("toolbar.saveAs") }),
							jsx("button", { key: "run", type: "button", onClick: runFlow, disabled: busy, "data-lingdong-run": "1", children: t("toolbar.run") }),
							jsx("button", { key: "stop", type: "button", onClick: stopRun, disabled: !busy, children: t("toolbar.stop") }),
							jsx("button", { key: "refresh", type: "button", onClick: refreshLibrary, children: t("toolbar.refresh") }),
							jsx("button", { key: "zo", type: "button", onClick: () => zoomBy(1 / 1.2), children: t("toolbar.zoomOut") }),
							jsx("button", { key: "zi", type: "button", onClick: () => zoomBy(1.2), children: t("toolbar.zoomIn") }),
							jsx("button", { key: "zr", type: "button", onClick: () => setView({ scale: 1 }), children: t("toolbar.reset") }),
							jsx("button", {
								key: "del",
								type: "button",
								onClick: deleteSelected,
								disabled: !current || SENTINEL_KINDS.indexOf(current.kind) !== -1,
								children: t("toolbar.delete")
							})
						]
					}),
					notice ? jsx("div", {
						key: "notice",
						"data-lingdong-notice": notice.tone,
						style: {
							padding: "4px 8px",
							fontSize: "11px",
							background: notice.tone === "error" ? "#fef2f2" : "#f0fdf4",
							color: notice.tone === "error" ? "#b91c1c" : "#166534",
							borderBottom: "1px solid #e2e8f0"
						},
						children: notice.text
					}) : null,
					jsxs("div", {
						key: "body",
						style: { display: "flex", flex: "1", minHeight: "0" },
						children: [
							jsxs("div", {
								key: "left",
								style: { width: "150px", padding: "8px", borderRight: "1px solid #e2e8f0", background: "#ffffff", overflowY: "auto" },
								children: [
									jsx("strong", { key: "lt", children: t("library.title") }),
									jsx("div", { key: "lc", style: { opacity: 0.6, marginBottom: "4px" }, children: library.phase === "loading" ? t("library.loading") : t("library.count").replace("{{count}}", String(library.flows.length)) }),
									library.phase === "error"
										? jsx("div", { key: "le", style: { color: "#b91c1c" }, children: t("library.error") })
										: (libraryRows.length ? jsx("div", { key: "ll", children: libraryRows }) : jsx("div", { key: "ln", style: { opacity: 0.6 }, children: t("library.empty") })),
									jsx("strong", { key: "pt", style: { display: "block", marginTop: "10px" }, children: t("palette.title") }),
									jsx("div", { key: "pl", children: paletteButtons }),
									jsx("div", { key: "ph", style: { opacity: 0.6, marginTop: "4px" }, children: t("palette.hint") })
								]
							}),
							jsx("div", {
								key: "canvas",
								onPointerMove: moveDrag,
								onPointerUp: endDrag,
								onPointerLeave: endDrag,
								onPointerDown: () => setSelectedId(""),
								"data-lingdong-canvas": "1",
								style: { position: "relative", flex: "1", overflow: "auto", background: "#f1f5f9" },
								children: jsxs("div", {
									style: {
										position: "relative",
										transform: "scale(" + view.scale + ")",
										transformOrigin: "0 0",
										width: NODE_W + COLUMN_X * 2,
										height: ROW_TOP * 2 + (nodes.length + 1) * ROW_GAP
									},
									children: [
										jsx("svg", {
											key: "edges",
											width: NODE_W + COLUMN_X * 2,
											height: ROW_TOP * 2 + (nodes.length + 1) * ROW_GAP,
											style: { position: "absolute", left: "0", top: "0", pointerEvents: "none" },
											children: edgeShapes
										}),
										jsx("div", { key: "nodes", children: nodeBoxes })
									]
								})
							}),
							jsxs("div", {
								key: "inspector",
								style: { width: "186px", padding: "8px", borderLeft: "1px solid #e2e8f0", background: "#ffffff", overflowY: "auto" },
								children: [
									jsx("strong", { key: "t", children: title }),
									jsx("div", { key: "b", children: inspector })
								]
							})
						]
					}),
					jsxs("div", {
						key: "status",
						"data-lingdong-status": "1",
						"data-lingdong-run-state": phase,
						style: { padding: "4px 8px", borderTop: "1px solid #e2e8f0", background: "#ffffff", opacity: 0.85 },
						children: [
							nodes.length + " " + t("status.nodes") + " / " + Math.max(0, nodes.length - 1) + " " + t("status.edges") + " / 0 " + t("status.modelCalls"),
							" · " + (doc.file || t("status.unsaved")),
							dirty ? " · " + t("status.dirty") : "",
							" · " + runSummary(run)
						]
					})
				]
			});
		}

		/**
		 * Register the tab type and the two pane seats.
		 * @param ctx - client root context.
		 */
		function apply(ctx) {
			ctx.effect(() => ctx.locale.register(NS, dictionaries), "ui-lingdong: dictionaries");
			ctx.effect(() => ctx.sidebarRightTabs.register({
				id: TAB_ID,
				kind: TAB_KIND,
				patterns: ["dsh-resource://lingdong-flow/**"],
				priority: "builtin",
				canOpen: (address) => typeof address === "string" && address.indexOf(ADDRESS_PREFIX) === 0,
				title: () => dictionaries.zh["tab.title"]
			}), "ui-lingdong: sidebar tab type");
			ctx.effect(() => ctx.slots.inject("sidebar.right.pane.tab", () => ctx.slots.register({
				name: "sidebar.right.pane.tab",
				key: TAB_ID,
				locale: NS
			}, LingdongFlowPane)), "ui-lingdong: pane body");
			ctx.effect(() => ctx.slots.inject("sidebar.right.pane.tab.title", () => ctx.slots.register({
				name: "sidebar.right.pane.tab.title",
				key: TAB_ID
			}, LingdongFlowTitle)), "ui-lingdong: pane title");
		}

		exports.apply = apply;
		exports.inject = inject;
		exports.TAB_KIND = TAB_KIND;
		exports.LingdongFlowPane = LingdongFlowPane;
		exports.PALETTE = PALETTE;
		exports.flowCodec = flowCodec;
		return module.exports;
	}
});
