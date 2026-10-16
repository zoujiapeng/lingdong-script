/**
 * Lingdong flow workbench - DSH Web GUI client plugin.
 *
 * Bundle format: DSH loads client plugins through window.__ModuleLoader__.load,
 * which hands the factory a require() resolving host modules. That wrapper is
 * the entire artifact, so this file is written by hand and no bundler is in
 * the loop (matching how DSH ships its own client bundles).
 *
 * The pipeline the canvas draws is the `steps` list of an `automation-ir/1`
 * document with `runtime.kind = "wps_replay"`, which is exactly the document a
 * recorded demonstration exports and exactly the document the offline replayer
 * executes. Opening an exported recording and saving an edited flow are
 * therefore the same code path, and one node compiles to one `.wps.json` step
 * with no translation layer in between.
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

		/** Undo depth. Deep enough for a session's editing, bounded on purpose. */
		const HISTORY_LIMIT = 60;

		/** Consecutive edits to one field inside this window collapse into one entry. */
		const MERGE_WINDOW_MS = 900;

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
				"toolbar.undo": "撤销",
				"toolbar.redo": "重做",
				"toolbar.fragment": "一句话生成片段",
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
				"inspector.empty": "选中一个节点后在此编辑；Shift 或 Ctrl 点击可多选。",
				"inspector.file": "文件名",
				"inspector.fileHint": "保存到脚本目录，必须以 .automation.json 结尾。",
				"inspector.label": "名称",
				"inspector.kind": "类型",
				"inspector.multi": "已选中 {{count}} 个节点",
				"inspector.multiHint": "删除、上移、下移都会作用于整个选择；目标窗口可一次写给全部可定位节点。",
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
				"fragment.title": "一句话生成片段",
				"fragment.hint": "用一句话描述要生成的步骤。这是一次可选的模型调用：不观察桌面、不读会话、没有执行权限，结果只是一份待确认的草稿。",
				"fragment.placeholder": "例如：点击坐标 420,300，等待 1 秒，然后输入文本 hello",
				"fragment.generate": "生成",
				"fragment.cancel": "取消",
				"fragment.apply": "插入流程",
				"fragment.discard": "放弃",
				"fragment.close": "关闭",
				"fragment.generating": "生成中…",
				"fragment.preview": "待插入的步骤（按执行顺序）",
				"fragment.usage": "本次生成消耗 {{calls}} 次模型调用",
				"fragment.insertAt": "将插入到第 {{index}} 位之后",
				"fragment.failed": "生成失败",
				"fragment.applied": "片段已插入，可用一次撤销整体回退",
				"fragment.branchRefused": "片段包含分支，线性管线无法精确表达，已拒绝插入",
				"fragment.noCompile": "片段里有无法精确编译的步骤，已拒绝插入",
				"status.nodes": "节点",
				"status.edges": "连线",
				"status.modelCalls": "离线运行 0 模型调用",
				"status.unsaved": "未保存",
				"status.untitled": "未命名流程",
				"status.dirty": "有未保存改动",
				"status.selection": "已选 {{count}}",
				"status.fragmentCalls": "片段生成 {{calls}} 次模型调用",
				"run.idle": "未运行",
				"run.validating": "运行时校验中…",
				"run.submitting": "提交中…",
				"run.running": "运行中",
				"run.done": "完成",
				"run.failed": "失败",
				"run.stopped": "已停止",
				"run.invalid": "运行时校验未通过，未提交运行",
				"notice.saved": "已保存",
				"notice.opened": "已打开",
				"notice.created": "已新建流程",
				"notice.loadFailed": "打开失败",
				"notice.saveFailed": "保存失败",
				"notice.runFailed": "运行失败",
				"notice.noFile": "请先填写文件名再保存",
				"notice.blocked": "该节点无法精确编译，运行已阻止",
				"notice.undone": "已撤销",
				"notice.redone": "已重做",
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
				"toolbar.undo": "Undo",
				"toolbar.redo": "Redo",
				"toolbar.fragment": "Describe a fragment",
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
				"inspector.empty": "Select a node to edit it; Shift or Ctrl click selects more.",
				"inspector.file": "File name",
				"inspector.fileHint": "Saved into the script library; must end in .automation.json.",
				"inspector.label": "Name",
				"inspector.kind": "Kind",
				"inspector.multi": "{{count}} nodes selected",
				"inspector.multiHint": "Delete, move up and move down act on the whole selection; a target window can be written to every addressable node at once.",
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
				"fragment.title": "Describe a fragment",
				"fragment.hint": "Describe the steps in one sentence. This is one opt-in model call: it observes no desktop, reads no conversation and has no execution authority; the result is only a draft to confirm.",
				"fragment.placeholder": "for example: click 420,300, wait 1 second, then type hello",
				"fragment.generate": "Generate",
				"fragment.cancel": "Cancel",
				"fragment.apply": "Insert into flow",
				"fragment.discard": "Discard",
				"fragment.close": "Close",
				"fragment.generating": "Generating…",
				"fragment.preview": "Steps to insert (in execution order)",
				"fragment.usage": "This generation used {{calls}} model calls",
				"fragment.insertAt": "Will be inserted after position {{index}}",
				"fragment.failed": "Generation failed",
				"fragment.applied": "Fragment inserted; one undo reverts all of it",
				"fragment.branchRefused": "The fragment branches, which a linear pipeline cannot express exactly; the insert was refused",
				"fragment.noCompile": "The fragment contains a step that cannot be compiled exactly; the insert was refused",
				"status.nodes": "nodes",
				"status.edges": "edges",
				"status.modelCalls": "offline runs use 0 model calls",
				"status.unsaved": "unsaved",
				"status.untitled": "Untitled flow",
				"status.dirty": "unsaved changes",
				"status.selection": "{{count}} selected",
				"status.fragmentCalls": "fragment helper used {{calls}} model calls",
				"run.idle": "not running",
				"run.validating": "validating with the runtime…",
				"run.submitting": "submitting…",
				"run.running": "running",
				"run.done": "done",
				"run.failed": "failed",
				"run.stopped": "stopped",
				"run.invalid": "the runtime rejected the flow; nothing was submitted",
				"notice.saved": "Saved",
				"notice.opened": "Opened",
				"notice.created": "New flow",
				"notice.loadFailed": "Open failed",
				"notice.saveFailed": "Save failed",
				"notice.runFailed": "Run failed",
				"notice.noFile": "Enter a file name before saving",
				"notice.blocked": "This node cannot be compiled exactly; the run was blocked",
				"notice.undone": "Undone",
				"notice.redone": "Redone",
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

		/** Fill `{{name}}` placeholders in one dictionary string. */
		function fill(template, values) {
			let out = String(template);
			const keys = Object.keys(values || {});
			for (let i = 0; i < keys.length; i += 1) {
				out = out.split("{{" + keys[i] + "}}").join(String(values[keys[i]]));
			}
			return out;
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

		/**
		 * Parameters the fragment helper may offer the model, per node kind.
		 *
		 * This is the small mechanical surface of an opt-in draft, not the full
		 * hand-editing catalog: the recorded fragment is left out because it is an
		 * opaque executable step, not something a model should author.
		 */
		const FRAGMENT_PARAM_KEYS = {
			click: ["x", "y", "button", "double", "window"],
			input_text: ["text", "window"],
			keyboard: ["keys", "repeat", "window"],
			wait: ["seconds"],
			condition: ["key", "value", "timeout_s"],
			screenshot: ["path"],
			run_command: ["command", "shell", "timeout_s"]
		};

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

		function clone(value) {
			return JSON.parse(JSON.stringify(value));
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
			const window = windowOfStep(step);
			if (!window && "target_window" in step) extra.target_window = step.target_window;
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
					label: descriptors[i].label,
					params: descriptors[i].params || {},
					x: COLUMN_X,
					y: ROW_TOP + (i + 1) * ROW_GAP
				});
			}
			nodes.push({
				id: "end",
				kind: "end",
				x: COLUMN_X,
				y: ROW_TOP + nodes.length * ROW_GAP,
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
			return {
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
				conditions: isObject(input.conditions) ? clone(input.conditions) : {},
				evidence: isObject(input.evidence) ? clone(input.evidence) : { required: [] },
				steps: compiled.steps,
				variables: isObject(input.variables) ? clone(input.variables) : {},
				runtime: { kind: "wps_replay" }
			};
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

		// ------------------------------------------------------------ fragments

		/**
		 * The request context for one opt-in fragment proposal.
		 *
		 * The runtime validates these field sets exactly, and it is also the shape
		 * the standalone Studio sends, so the same helper serves both editors.
		 * @param nodes - current canvas nodes.
		 * @returns `{ document, catalog }`.
		 */
		function fragmentContext(nodes) {
			const list = Array.isArray(nodes) ? nodes : [];
			const kinds = [];
			for (let i = 0; i < list.length; i += 1) {
				if (kinds.indexOf(list[i].kind) === -1) kinds.push(list[i].kind);
			}
			const types = Object.keys(FRAGMENT_PARAM_KEYS).sort();
			const catalog = [];
			for (let i = 0; i < types.length; i += 1) {
				const kind = types[i];
				const defaults = defaultParams(kind);
				const params = {};
				const keys = FRAGMENT_PARAM_KEYS[kind];
				for (let k = 0; k < keys.length; k += 1) {
					params[keys[k]] = Object.prototype.hasOwnProperty.call(defaults, keys[k]) ? defaults[keys[k]] : null;
				}
				catalog.push({
					type: kind,
					label: labelOf(kind),
					params: params,
					// The canvas is one ordered pipeline, so every node has exactly
					// one successor branch. Offering more would invite a fragment the
					// canvas cannot place.
					output_ports: ["next"]
				});
			}
			return {
				document: {
					node_count: list.length,
					edge_count: Math.max(0, list.length - 1),
					node_types: kinds.slice().sort()
				},
				catalog: catalog
			};
		}

		/**
		 * Validate one proposal the helper returned.
		 *
		 * This mirrors the runtime's own normalization instead of trusting it: the
		 * host validated the model's call, and this validates that what came back
		 * is something this canvas can insert.
		 * @param value - the job result.
		 * @returns the normalized proposal, or throws.
		 */
		function normalizeFragment(value) {
			const proposal = isObject(value) ? value : null;
			if (!proposal) throw FlowError("流程片段不是对象");
			if (proposal.format !== "flow-fragment-proposal/1") {
				throw FlowError("流程片段格式不受支持：" + String(proposal.format));
			}
			const rawNodes = proposal.nodes;
			const rawEdges = proposal.edges;
			if (!Array.isArray(rawNodes) || rawNodes.length < 1 || rawNodes.length > 32) {
				throw FlowError("流程片段必须包含 1 到 32 个节点");
			}
			if (!Array.isArray(rawEdges) || rawEdges.length > 64) {
				throw FlowError("流程片段连线数量无效");
			}
			const allowed = Object.keys(FRAGMENT_PARAM_KEYS);
			const nodes = [];
			const ids = {};
			for (let i = 0; i < rawNodes.length; i += 1) {
				const raw = rawNodes[i];
				if (!isObject(raw)) throw FlowError("流程片段节点 " + (i + 1) + " 不是对象");
				const localId = String(raw.local_id == null ? "" : raw.local_id).trim();
				if (!localId || localId.length > 64) throw FlowError("流程片段节点 ID 无效");
				if (ids[localId]) throw FlowError("流程片段包含重复节点 ID：" + localId);
				const type = String(raw.type == null ? "" : raw.type);
				if (allowed.indexOf(type) === -1) throw FlowError("节点 " + localId + " 使用了不可插入的类型：" + type);
				const params = isObject(raw.params) ? raw.params : {};
				const keys = Object.keys(params);
				const permitted = FRAGMENT_PARAM_KEYS[type];
				for (let k = 0; k < keys.length; k += 1) {
					if (permitted.indexOf(keys[k]) === -1) {
						throw FlowError("节点 " + localId + " 使用了未开放参数：" + keys[k]);
					}
				}
				ids[localId] = true;
				nodes.push({
					local_id: localId,
					kind: type,
					label: typeof raw.label === "string" && raw.label.trim() ? raw.label.trim().slice(0, 120) : "",
					params: params
				});
			}
			const edges = [];
			const branches = {};
			for (let i = 0; i < rawEdges.length; i += 1) {
				const raw = rawEdges[i];
				if (!isObject(raw)) throw FlowError("流程片段连线 " + (i + 1) + " 不是对象");
				const source = String(raw.source == null ? "" : raw.source).trim();
				const target = String(raw.target == null ? "" : raw.target).trim();
				const port = String(raw.source_port == null ? "" : raw.source_port).trim();
				if (!ids[source] || !ids[target]) throw FlowError("连线 " + source + " → " + target + " 引用了片段之外的节点");
				if (source === target) throw FlowError("连线 " + source + " 不能指向节点自身");
				if (port !== "next") throw FlowError("节点 " + source + " 没有输出端口 " + port);
				if (branches[source]) throw FlowError("节点 " + source + " 的分支只能连接一个目标");
				branches[source] = true;
				edges.push({ source: source, target: target, source_port: port });
			}
			return {
				format: "flow-fragment-proposal/1",
				summary: typeof proposal.summary === "string" ? proposal.summary.trim().slice(0, 1000) : "",
				usage: isObject(proposal.proposal_usage) ? proposal.proposal_usage : null,
				nodes: nodes,
				edges: edges
			};
		}

		/**
		 * Order a fragment for a linear pipeline.
		 *
		 * With one output port per node every edge means exactly "run the source
		 * before the target", so any topological order executes the fragment's own
		 * constraints faithfully; ties are broken by the model's declaration order
		 * so the result is stable. Anything the canvas still cannot compile - a
		 * condition with an unknown predicate, a click without coordinates - is
		 * refused here rather than inserted as a node that would mean something
		 * else.
		 * @param proposal - a normalized proposal.
		 * @returns node descriptors in execution order.
		 */
		function linearizeFragment(proposal) {
			const indegree = {};
			const outgoing = {};
			for (let i = 0; i < proposal.nodes.length; i += 1) {
				indegree[proposal.nodes[i].local_id] = 0;
				outgoing[proposal.nodes[i].local_id] = [];
			}
			for (let i = 0; i < proposal.edges.length; i += 1) {
				outgoing[proposal.edges[i].source].push(proposal.edges[i].target);
				indegree[proposal.edges[i].target] += 1;
			}
			const out = [];
			const placed = {};
			while (out.length < proposal.nodes.length) {
				let picked = null;
				for (let i = 0; i < proposal.nodes.length; i += 1) {
					const candidate = proposal.nodes[i];
					if (!placed[candidate.local_id] && indegree[candidate.local_id] === 0) {
						picked = candidate;
						break;
					}
				}
				if (!picked) throw FlowError("流程片段包含循环连线");
				placed[picked.local_id] = true;
				out.push(picked);
				for (let i = 0; i < outgoing[picked.local_id].length; i += 1) {
					indegree[outgoing[picked.local_id][i]] -= 1;
				}
			}
			const descriptors = [];
			for (let i = 0; i < out.length; i += 1) {
				const draft = out[i];
				const params = Object.assign(defaultParams(draft.kind), clone(draft.params));
				if (draft.kind === "condition") {
					const cond = conditionOf(params);
					if (!cond || !params.key || CONDITION_KEYS.indexOf(params.key) === -1) {
						throw FlowError(translate("fragment.noCompile") + "（条件谓词不精确）");
					}
				}
				// The proposal is only insertable if every node compiles exactly.
				const probe = { id: "fragment-draft-" + i, kind: draft.kind, label: draft.label, params: params };
				let compiles = false;
				try {
					compiles = compileNodes([probe]).steps.length > 0;
				} catch (error) {
					compiles = false;
				}
				if (!compiles) throw FlowError(translate("fragment.noCompile"));
				descriptors.push({ kind: draft.kind, label: draft.label, params: params });
			}
			return descriptors;
		}

		/** Every codec entry point, exposed so the bundle can be verified offline. */
		const flowCodec = {
			PALETTE: PALETTE,
			CONDITION_KEYS: CONDITION_KEYS,
			SENTINEL_KINDS: SENTINEL_KINDS,
			FRAGMENT_PARAM_KEYS: FRAGMENT_PARAM_KEYS,
			compileNodes: compileNodes,
			explodeSteps: explodeSteps,
			layoutNodes: layoutNodes,
			defaultParams: defaultParams,
			toDocument: toDocument,
			fromDocument: fromDocument,
			fragmentContext: fragmentContext,
			normalizeFragment: normalizeFragment,
			linearizeFragment: linearizeFragment,
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

		/** The empty document envelope a fresh canvas starts from. */
		function emptyDoc() {
			return { id: "", name: "", created: 0, task: "", conditions: {}, evidence: { required: [] }, variables: {}, file: "" };
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
			const docState = react.useState(emptyDoc);
			const doc = docState[0];
			const setDoc = docState[1];
			const dirtyState = react.useState(false);
			const dirty = dirtyState[0];
			const setDirty = dirtyState[1];
			const selectionState = react.useState([]);
			const selection = selectionState[0];
			const setSelection = selectionState[1];
			const viewState = react.useState({ scale: 1 });
			const view = viewState[0];
			const setView = viewState[1];
			const dragState = react.useState(null);
			const drag = dragState[0];
			const setDrag = dragState[1];
			const bandState = react.useState(null);
			const band = bandState[0];
			const setBand = bandState[1];
			const historyState = react.useState({ past: [], future: [] });
			const history = historyState[0];
			const setHistory = historyState[1];
			const libraryState = react.useState({ phase: "idle", flows: [], error: "" });
			const library = libraryState[0];
			const setLibrary = libraryState[1];
			const noticeState = react.useState(null);
			const notice = noticeState[0];
			const setNotice = noticeState[1];
			const runState = react.useState({ state: "idle", id: "", error: "", nodes: {} });
			const run = runState[0];
			const setRun = runState[1];
			const fragmentState = react.useState({ open: false, goal: "", state: "idle", error: "", proposal: null, calls: null, jobId: "" });
			const fragment = fragmentState[0];
			const setFragment = fragmentState[1];
			const pollRef = react.useRef(null);
			const fragmentPollRef = react.useRef(null);
			const runRef = react.useRef(run);
			runRef.current = run;
			const mergeRef = react.useRef({ key: "", at: 0 });
			const canvasRef = react.useRef(null);

			function t(key) {
				return translate(key);
			}

			const byId = {};
			for (let i = 0; i < nodes.length; i += 1) byId[nodes[i].id] = nodes[i];
			const executable = nodes.filter((node) => SENTINEL_KINDS.indexOf(node.kind) === -1);
			const primary = selection.length ? byId[selection[selection.length - 1]] : null;

			function isSelected(id) {
				return selection.indexOf(id) !== -1;
			}

			function selectOnly(id) {
				setSelection(id ? [id] : []);
			}

			function toggleSelect(id) {
				setSelection((previous) => {
					if (previous.indexOf(id) !== -1) return previous.filter((item) => item !== id);
					return previous.concat([id]);
				});
			}

			// ---- history ------------------------------------------------------

			/** A deep-enough copy of the canvas to restore exactly. */
			function snapshot() {
				return {
					nodes: nodes.map((node) => ({
						id: node.id,
						kind: node.kind,
						label: node.label,
						x: node.x,
						y: node.y,
						params: clone(node.params)
					})),
					doc: clone(doc),
					selection: selection.slice()
				};
			}

			/**
			 * Record one undo checkpoint before a change.
			 *
			 * `mergeKey` collapses a burst of edits to the same field into a single
			 * entry, so typing a coordinate is one undo rather than one per keystroke,
			 * while a structural change passes no key and always gets its own.
			 */
			function pushHistory(mergeKey) {
				const now = Date.now();
				if (mergeKey && mergeRef.current.key === mergeKey && now - mergeRef.current.at < MERGE_WINDOW_MS) {
					mergeRef.current.at = now;
					return;
				}
				mergeRef.current = { key: mergeKey || "", at: now };
				const mark = snapshot();
				setHistory((previous) => ({
					past: previous.past.concat([mark]).slice(-HISTORY_LIMIT),
					future: []
				}));
			}

			function applySnapshot(mark) {
				setNodes(mark.nodes.map((node) => Object.assign({}, node, { params: clone(node.params) })));
				setDoc(clone(mark.doc));
				setSelection(mark.selection ? mark.selection.slice() : []);
				setDirty(true);
				setRun((previous) => Object.assign({}, previous, { nodes: {} }));
			}

			function undo() {
				if (!history.past.length) return;
				const mark = history.past[history.past.length - 1];
				const current = snapshot();
				setHistory({
					past: history.past.slice(0, -1),
					future: [current].concat(history.future).slice(0, HISTORY_LIMIT)
				});
				mergeRef.current = { key: "", at: 0 };
				applySnapshot(mark);
				setNotice({ tone: "info", text: t("notice.undone") });
			}

			function redo() {
				if (!history.future.length) return;
				const mark = history.future[0];
				const current = snapshot();
				setHistory({
					past: history.past.concat([current]).slice(-HISTORY_LIMIT),
					future: history.future.slice(1)
				});
				mergeRef.current = { key: "", at: 0 };
				applySnapshot(mark);
				setNotice({ tone: "info", text: t("notice.redone") });
			}

			// ---- library and documents ----------------------------------------

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
					if (fragmentPollRef.current !== null) clearTimeout(fragmentPollRef.current);
				};
			}, []);

			function resetRun() {
				setRun({ state: "idle", id: "", error: "", nodes: {} });
			}

			function newFlow() {
				pushHistory();
				setNodes(defaultNodes());
				setDoc(emptyDoc());
				setSelection([]);
				setDirty(false);
				resetRun();
				setNotice({ tone: "info", text: t("notice.created") });
			}

			async function openFlow(entry) {
				try {
					const document = await bridge("GET", "/flows/" + encodeURIComponent(entry.file));
					const loaded = flowCodec.fromDocument(document);
					pushHistory();
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
					setSelection([]);
					setDirty(false);
					resetRun();
					setNotice({ tone: "info", text: t("notice.opened") + "：" + entry.file });
				} catch (error) {
					setNotice({ tone: "error", text: t("notice.loadFailed") + "：" + String(error.message || error) });
				}
			}

			/** Build the document the canvas currently represents. */
			function currentDocument() {
				return flowCodec.toDocument({
					id: doc.id,
					name: doc.name || String(doc.file).replace(/\.automation\.json$/, ""),
					created: doc.created,
					task: doc.task,
					conditions: doc.conditions,
					evidence: doc.evidence,
					variables: doc.variables,
					nodes: nodes
				});
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

			// ---- structural edits ---------------------------------------------

			/** Replace the parameters of one node. */
			function patchNode(id, patch, mergeKey) {
				pushHistory(mergeKey);
				setNodes(nodes.map((node) => (
					node.id === id ? Object.assign({}, node, { params: Object.assign({}, node.params, patch) }) : node
				)));
				setDirty(true);
			}

			/** Write one parameter value to every selected node that can hold it. */
			function patchSelection(patch, mergeKey) {
				pushHistory(mergeKey);
				setNodes(nodes.map((node) => (
					isSelected(node.id) ? Object.assign({}, node, { params: Object.assign({}, node.params, patch) }) : node
				)));
				setDirty(true);
			}

			function renameSelected(value) {
				if (!primary) return;
				pushHistory("label:" + primary.id);
				setNodes(nodes.map((node) => (node.id === primary.id ? Object.assign({}, node, { label: value }) : node)));
				setDirty(true);
			}

			/** Insert one descriptor after the primary selection (or before `end`). */
			function insertDescriptors(descriptors) {
				const fresh = descriptors.map((descriptor, index) => ({
					id: "n" + Date.now().toString(36) + "-" + index + Math.random().toString(36).slice(2, 5),
					kind: descriptor.kind,
					label: descriptor.label,
					params: descriptor.params || defaultParams(descriptor.kind),
					x: COLUMN_X,
					y: ROW_TOP
				}));
				let at = nodes.length - 1;
				for (let i = 0; i < nodes.length; i += 1) {
					if (nodes[i].id === primary?.id) at = i + 1;
				}
				if (at >= nodes.length) at = nodes.length - 1;
				setNodes(snap(nodes.slice(0, at).concat(fresh, nodes.slice(at))));
				setSelection(fresh.map((node) => node.id));
				setDirty(true);
				resetRun();
				return at;
			}

			function addNode(kind) {
				pushHistory();
				insertDescriptors([{ kind: kind, label: undefined, params: defaultParams(kind) }]);
			}

			function deleteSelected() {
				const removable = selection.filter((id) => {
					const node = byId[id];
					return node && SENTINEL_KINDS.indexOf(node.kind) === -1;
				});
				if (!removable.length) return;
				pushHistory();
				setNodes(snap(nodes.filter((node) => removable.indexOf(node.id) === -1)));
				setSelection([]);
				setDirty(true);
				resetRun();
			}

			/**
			 * Move the whole selection one slot up or down.
			 *
			 * The selection keeps its internal order and stays contiguous, which is
			 * what a pipeline move means; a single node is just the one-node case.
			 */
			function moveSelection(delta) {
				const moving = executable.filter((node) => isSelected(node.id));
				if (!moving.length) return;
				const rest = executable.filter((node) => !isSelected(node.id));
				const first = executable.indexOf(moving[0]);
				const last = executable.indexOf(moving[moving.length - 1]);
				const target = delta < 0 ? first - 1 : last + 1;
				if (target < 0 || target > executable.length - 1) return;
				const neighbour = executable[target];
				pushHistory();
				const before = rest.slice(0, rest.indexOf(neighbour) + (delta < 0 ? 0 : 1));
				const after = rest.slice(rest.indexOf(neighbour) + (delta < 0 ? 0 : 1));
				setNodes(snap([nodes[0]].concat(before, moving, after, [nodes[nodes.length - 1]])));
				setDirty(true);
			}

			function zoomBy(factor) {
				setView({ scale: Math.min(2.5, Math.max(0.4, view.scale * factor)) });
			}

			// ---- pointer interaction ------------------------------------------

			/** Flow coordinates of a client point inside the scrolling canvas. */
			function flowPoint(event) {
				const element = canvasRef.current;
				if (!element) return { x: 0, y: 0 };
				const rect = element.getBoundingClientRect();
				return {
					x: (event.clientX - rect.left + element.scrollLeft) / view.scale,
					y: (event.clientY - rect.top + element.scrollTop) / view.scale
				};
			}

			function startDrag(event, node) {
				if (event && event.stopPropagation) event.stopPropagation();
				if (SENTINEL_KINDS.indexOf(node.kind) !== -1) {
					selectOnly(node.id);
					return;
				}
				if (event && (event.shiftKey || event.ctrlKey || event.metaKey)) {
					toggleSelect(node.id);
					return;
				}
				if (!isSelected(node.id)) selectOnly(node.id);
				setDrag({
					id: node.id,
					originY: node.y,
					fromY: event && typeof event.clientY === "number" ? event.clientY : 0
				});
			}

			function moveDrag(event) {
				if (!drag || !event || typeof event.clientY !== "number") return;
				const dy = (event.clientY - drag.fromY) / view.scale;
				setNodes(nodes.map((node) => (node.id === drag.id ? Object.assign({}, node, { y: drag.originY + dy }) : node)));
			}

			/**
			 * Dropping a node reorders the pipeline: vertical position is the order.
			 *
			 * A drag that started on a selected node moves the whole selection as one
			 * block, so a multi-select drag is still a single undo entry.
			 */
			function endDrag() {
				if (!drag) return;
				const moved = nodes.filter((node) => node.id === drag.id)[0];
				setDrag(null);
				if (!moved) return;
				const mark = snapshot();
				const moving = executable.filter((node) => isSelected(node.id));
				const block = moving.length && isSelected(moved.id) ? moving : [moved];
				const rest = executable.filter((node) => block.indexOf(node) === -1);
				const threshold = centerOf(moved).y;
				let at = 0;
				for (let i = 0; i < rest.length; i += 1) {
					if (centerOf(rest[i]).y < threshold) at = i + 1;
				}
				const ordered = rest.slice(0, at).concat(block, rest.slice(at));
				const changed = ordered.some((node, index) => node.id !== executable[index].id);
				if (!changed) {
					setNodes(snap(nodes));
					return;
				}
				setHistory((previous) => ({ past: previous.past.concat([mark]).slice(-HISTORY_LIMIT), future: [] }));
				mergeRef.current = { key: "", at: 0 };
				setNodes(snap([nodes[0]].concat(ordered, [nodes[nodes.length - 1]])));
				setDirty(true);
			}

			function startBand(event) {
				// A press that landed on a node belongs to that node, whichever
				// descendant element of the canvas the browser picked.
				const target = event.target;
				if (target && typeof target.closest === "function" && target.closest("[data-lingdong-node]")) return;
				const point = flowPoint(event);
				if (!event.shiftKey) setSelection([]);
				setBand({ x0: point.x, y0: point.y, x1: point.x, y1: point.y });
			}

			function moveBand(event) {
				if (!band) return;
				const point = flowPoint(event);
				setBand({ x0: band.x0, y0: band.y0, x1: point.x, y1: point.y });
			}

			/** Select every node the rubber band touches, in pipeline order. */
			function endBand() {
				if (!band) return;
				const left = Math.min(band.x0, band.x1);
				const right = Math.max(band.x0, band.x1);
				const top = Math.min(band.y0, band.y1);
				const bottom = Math.max(band.y0, band.y1);
				const hit = [];
				for (let i = 0; i < nodes.length; i += 1) {
					const node = nodes[i];
					const overlaps = node.x < right && node.x + NODE_W > left && node.y < bottom && node.y + NODE_H > top;
					if (overlaps) hit.push(node.id);
				}
				setBand(null);
				if (hit.length) setSelection(hit);
			}

			// ---- keyboard ------------------------------------------------------

			/**
			 * Workbench shortcuts, scoped to this pane.
			 *
			 * The handler sits on the workbench root, so it only sees keys pressed
			 * while the pane or one of its controls has focus; DSH's own shortcuts
			 * elsewhere are never touched. Inside a text field the browser's native
			 * undo wins, which is what someone typing a parameter expects.
			 */
			function onKeyDown(event) {
				const tag = String((event.target && event.target.tagName) || "").toLowerCase();
				const typing = tag === "input" || tag === "textarea" || tag === "select";
				if (event.key === "Escape") {
					if (fragment.open) setFragment((previous) => Object.assign({}, previous, { open: false }));
					else setSelection([]);
					return;
				}
				if (typing) return;
				const accel = event.ctrlKey || event.metaKey;
				const key = String(event.key || "").toLowerCase();
				if (accel && key === "z" && !event.shiftKey) {
					event.preventDefault();
					undo();
					return;
				}
				if (accel && (key === "y" || (key === "z" && event.shiftKey))) {
					event.preventDefault();
					redo();
					return;
				}
				if (accel && key === "a") {
					event.preventDefault();
					setSelection(nodes.filter((node) => SENTINEL_KINDS.indexOf(node.kind) === -1).map((node) => node.id));
					return;
				}
				if (accel && key === "s") {
					event.preventDefault();
					saveFlow();
					return;
				}
				if (key === "delete" || key === "backspace") {
					event.preventDefault();
					deleteSelected();
				}
			}

			// ---- running -------------------------------------------------------

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
				const phase = runRef.current.state;
				if (phase === "running" || phase === "submitting" || phase === "validating") return;
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
				const runId = started.id;
				setRun({ state: "running", id: runId, error: "", nodes: {} });

				/** Poll until the runtime reports a terminal state. */
				async function poll() {
					pollRef.current = null;
					let answer;
					try {
						answer = await bridge("GET", "/runs/" + encodeURIComponent(runId) + "?kind=" + kind);
					} catch (error) {
						setRun({ state: "failed", id: runId, error: String(error.message || error), nodes: {} });
						return;
					}
					const job = kind === "replay" ? answer.job : answer.run;
					const status = String((job && job.status) || answer.status || "");
					if (["done", "failed", "stopped", "canceled", "cancelled"].indexOf(status) !== -1) {
						const failed = status !== "done" || (job && job.ok === false);
						setRun({
							state: failed ? "failed" : "done",
							id: runId,
							error: failed ? String((job && job.error) || "") : "",
							nodes: applyRunResult(job, compiled.owners)
						});
						return;
					}
					setRun((previous) => Object.assign({}, previous, { state: "running", id: runId }));
					pollRef.current = setTimeout(poll, 400);
				}
				poll();
			}

			// ---- fragment helper ----------------------------------------------

			function closeFragment() {
				if (fragmentPollRef.current !== null) {
					clearTimeout(fragmentPollRef.current);
					fragmentPollRef.current = null;
				}
				setFragment({ open: false, goal: "", state: "idle", error: "", proposal: null, calls: null, jobId: "" });
			}

			/**
			 * Ask the host for one fragment draft for the sentence in the box.
			 *
			 * This is the only model call the workbench can make, it happens only
			 * when the user asks for it, and its result is inert until the user
			 * inserts it.
			 */
			async function generateFragment() {
				const goal = String(fragment.goal || "").trim();
				if (!goal || fragment.state === "generating") return;
				setFragment((previous) => Object.assign({}, previous, { state: "generating", error: "", proposal: null }));
				const context = flowCodec.fragmentContext(nodes);
				let started;
				try {
					started = await bridge("POST", "/fragments", {
						goal: goal,
						document: context.document,
						catalog: context.catalog
					});
				} catch (error) {
					setFragment((previous) => Object.assign({}, previous, { state: "failed", error: String(error.message || error) }));
					return;
				}
				const jobId = started.job_id;
				setFragment((previous) => Object.assign({}, previous, { state: "generating", jobId: jobId }));

				async function poll() {
					fragmentPollRef.current = null;
					let answer;
					try {
						answer = await bridge("GET", "/fragments/" + encodeURIComponent(jobId));
					} catch (error) {
						setFragment((previous) => Object.assign({}, previous, { state: "failed", error: String(error.message || error) }));
						return;
					}
					const status = String(answer.status || "");
					if (status === "done") {
						try {
							const proposal = flowCodec.normalizeFragment(answer.proposal);
							// Linearity is checked now, not at insert time, so the user
							// never confirms something that cannot be placed.
							flowCodec.linearizeFragment(proposal);
							setFragment((previous) => Object.assign({}, previous, {
								state: "ready",
								proposal: proposal,
								error: "",
								calls: proposal.usage && proposal.usage.model_calls != null ? proposal.usage.model_calls : null
							}));
						} catch (error) {
							setFragment((previous) => Object.assign({}, previous, { state: "failed", error: String(error.message || error) }));
						}
						return;
					}
					if (status === "failed" || status === "canceled") {
						setFragment((previous) => Object.assign({}, previous, {
							state: "failed",
							error: String(answer.error || t("fragment.failed"))
						}));
						return;
					}
					fragmentPollRef.current = setTimeout(poll, 300);
				}
				poll();
			}

			/** Insert the confirmed draft as exactly one undo checkpoint. */
			function applyFragment() {
				if (!fragment.proposal) return;
				let descriptors;
				try {
					descriptors = flowCodec.linearizeFragment(fragment.proposal);
				} catch (error) {
					setFragment((previous) => Object.assign({}, previous, { state: "failed", error: String(error.message || error) }));
					return;
				}
				pushHistory();
				insertDescriptors(descriptors);
				closeFragment();
				setNotice({ tone: "info", text: t("fragment.applied") });
			}

			// ---- derived render data -------------------------------------------

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
				const chosen = isSelected(node.id);
				const outcome = run.nodes[node.id];
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
					"data-lingdong-selected": chosen ? "1" : "0",
					onPointerDown: (event) => startDrag(event, node),
					style: {
						position: "absolute",
						left: node.x + "px",
						top: node.y + "px",
						width: NODE_W + "px",
						height: NODE_H + "px",
						borderRadius: "8px",
						border: chosen ? "2px solid #2563eb" : "1px solid #cbd5e1",
						borderLeft: "4px solid " + colorOf(node.kind),
						background: chosen ? "#eff6ff" : "#ffffff",
						display: "flex",
						alignItems: "center",
						padding: "0 10px",
						fontSize: "12px",
						cursor: SENTINEL_KINDS.indexOf(node.kind) === -1 ? "grab" : "default",
						boxSizing: "border-box",
						userSelect: "none"
					},
					children: [
						jsx("span", {
							key: "l",
							style: { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
							children: node.label && node.label !== labelOf(node.kind) ? node.label : labelOf(node.kind)
						}),
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

			/** One labelled input row. */
			function field(name, type, value, onChange, mergeKey) {
				const common = {
					style: { width: "100%", marginTop: "2px" },
					"data-lingdong-field": name
				};
				const commit = (next) => onChange(next, mergeKey);
				if (type === "textarea") {
					return jsxs("label", { key: name, style: { display: "block", marginTop: "4px" }, children: [
						t("inspector." + name),
						jsx("textarea", Object.assign({}, common, { value: value == null ? "" : String(value), rows: 3, onChange: (event) => commit(event.target.value) }))
					] });
				}
				if (type === "number") {
					return jsxs("label", { key: name, style: { display: "block", marginTop: "4px" }, children: [
						t("inspector." + name),
						jsx("input", Object.assign({}, common, { type: "number", value: value == null ? "" : String(value), onChange: (event) => commit(event.target.value) }))
					] });
				}
				return jsxs("label", { key: name, style: { display: "block", marginTop: "4px" }, children: [
					t("inspector." + name),
					jsx("input", Object.assign({}, common, { value: value == null ? "" : String(value), onChange: (event) => commit(event.target.value) }))
				] });
			}

			/** One labelled select row. */
			function select(name, options, value, onChange) {
				const children = [];
				for (let i = 0; i < options.length; i += 1) {
					children.push(jsx("option", { key: options[i], value: options[i], children: options[i] }));
				}
				return jsxs("label", { key: name, style: { display: "block", marginTop: "4px" }, children: [
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
				return jsxs("label", { key: name, style: { display: "block", marginTop: "4px" }, children: [
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

			const inspector = [];
			inspector.push(jsxs("label", { key: "file", style: { display: "block", marginBottom: "6px" }, children: [
				t("inspector.file"),
				jsx("input", {
					value: doc.file,
					"data-lingdong-field": "file",
					placeholder: "my-flow.automation.json",
					onChange: (event) => {
						const value = event.target.value;
						pushHistory("file");
						setDoc((previous) => Object.assign({}, previous, { file: value }));
						setDirty(true);
					},
					style: { width: "100%", marginTop: "2px", fontFamily: "monospace", fontSize: "11px" }
				})
			] }));
			inspector.push(jsx("div", { key: "fileHint", style: { opacity: 0.6, marginBottom: "6px" }, children: t("inspector.fileHint") }));

			if (selection.length > 1) {
				inspector.push(jsx("div", { key: "multi", "data-lingdong-multi": String(selection.length), children: fill(t("inspector.multi"), { count: selection.length }) }));
				inspector.push(hint("multiHint"));
				// Uncontrolled on purpose: a bulk value has no single current value to
				// show, and re-keying on the selection clears whatever was typed before.
				inspector.push(jsxs("label", {
					key: "multiWindow",
					style: { display: "block", marginTop: "4px" },
					children: [
						t("inspector.window"),
						jsx("input", {
							key: "multi-window-" + selection.join(","),
							defaultValue: "",
							"data-lingdong-field": "window",
							onChange: (event) => patchSelection({ window: event.target.value }, "multi:window"),
							style: { width: "100%", marginTop: "2px" }
						})
					]
				}));
				inspector.push(hint("windowHint"));
				inspector.push(jsxs("div", { key: "move", style: { marginTop: "6px", display: "flex", gap: "4px" }, children: [
					jsx("button", { type: "button", onClick: () => moveSelection(-1), children: t("inspector.moveUp") }),
					jsx("button", { type: "button", onClick: () => moveSelection(1), children: t("inspector.moveDown") })
				] }));
			} else if (primary) {
				const params = primary.params || {};
				inspector.push(jsx("div", { key: "kind", children: t("inspector.kind") + "：" + labelOf(primary.kind) }));
				if (SENTINEL_KINDS.indexOf(primary.kind) === -1) {
					inspector.push(jsxs("label", { key: "label", style: { display: "block", marginTop: "4px" }, children: [
						t("inspector.label"),
						jsx("input", {
							value: primary.label == null ? "" : primary.label,
							"data-lingdong-field": "label",
							onChange: (event) => renameSelected(event.target.value),
							style: { width: "100%", marginTop: "2px" }
						})
					] }));
					if (primary.kind === "click") {
						inspector.push(field("x", "number", params.x, (value) => patchNode(primary.id, { x: intOr(value, 0) }, "x:" + primary.id)));
						inspector.push(field("y", "number", params.y, (value) => patchNode(primary.id, { y: intOr(value, 0) }, "y:" + primary.id)));
						inspector.push(select("button", ["left", "right", "middle"], params.button || "left", (value) => patchNode(primary.id, { button: value })));
						inspector.push(checkbox("double", params.double === true, (value) => patchNode(primary.id, { double: value })));
					} else if (primary.kind === "input_text") {
						inspector.push(field("text", "textarea", params.text, (value) => patchNode(primary.id, { text: value }, "text:" + primary.id)));
					} else if (primary.kind === "keyboard") {
						inspector.push(field("keys", "text", params.keys, (value) => patchNode(primary.id, { keys: value }, "keys:" + primary.id)));
						inspector.push(hint("keysHint"));
					} else if (primary.kind === "wait") {
						inspector.push(field("seconds", "number", params.seconds, (value) => patchNode(primary.id, { seconds: Math.max(0, numberOr(value, 0)) }, "seconds:" + primary.id)));
					} else if (primary.kind === "condition") {
						if (params.key) {
							inspector.push(select("conditionKey", CONDITION_KEYS, params.key, (value) => patchNode(primary.id, { key: value })));
							inspector.push(field("conditionValue", "text", params.value, (value) => patchNode(primary.id, { value: value }, "value:" + primary.id)));
						} else {
							inspector.push(hint("conditionOpaque"));
						}
						inspector.push(field("timeout", "number", params.timeout_s, (value) => patchNode(primary.id, { timeout_s: Math.max(0, numberOr(value, 0)) }, "timeout:" + primary.id)));
					} else if (primary.kind === "screenshot") {
						inspector.push(field("path", "text", params.path, (value) => patchNode(primary.id, { path: value }, "path:" + primary.id)));
						inspector.push(hint("pathHint"));
					} else if (primary.kind === "run_command") {
						inspector.push(field("command", "textarea", params.command, (value) => patchNode(primary.id, { command: value }, "command:" + primary.id)));
						inspector.push(field("shell", "text", params.shell, (value) => patchNode(primary.id, { shell: value }, "shell:" + primary.id)));
						inspector.push(field("timeout", "number", params.timeout_s, (value) => patchNode(primary.id, { timeout_s: Math.max(0, numberOr(value, 0)) }, "cmdtimeout:" + primary.id)));
					} else if (primary.kind === "replay_step") {
						inspector.push(jsxs("label", { key: "step", style: { display: "block", marginTop: "4px" }, children: [
							t("inspector.stepJson"),
							jsx("textarea", {
								value: JSON.stringify(params.step || {}, null, 1),
								rows: 6,
								"data-lingdong-field": "step",
								onChange: (event) => {
									try {
										patchNode(primary.id, { step: JSON.parse(event.target.value) }, "step:" + primary.id);
									} catch (error) {
										// Keep the last valid step while the text is mid-edit.
									}
								},
								style: { width: "100%", fontFamily: "monospace", fontSize: "11px" }
							})
						] }));
					}
					if (TARGETABLE[primary.kind]) {
						inspector.push(field("window", "text", params.window, (value) => patchNode(primary.id, { window: value }, "window:" + primary.id)));
						inspector.push(hint("windowHint"));
					}
					if (isObject(params.extra) && Object.keys(params.extra).length) {
						inspector.push(jsxs("div", {
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
						jsx("button", { type: "button", onClick: () => moveSelection(-1), children: t("inspector.moveUp") }),
						jsx("button", { type: "button", onClick: () => moveSelection(1), children: t("inspector.moveDown") })
					] }));
					inspector.push(jsx("div", { key: "pos", style: { opacity: 0.6, marginTop: "4px" }, children: t("inspector.position") + "：" + (executable.indexOf(primary) + 1) + " / " + executable.length }));
				}
			} else {
				inspector.push(jsx("span", { key: "empty", style: { opacity: 0.6 }, children: t("inspector.empty") }));
			}

			// ---- fragment dialog -----------------------------------------------

			const fragmentPreview = [];
			if (fragment.proposal) {
				let descriptors = [];
				let linearError = "";
				try {
					descriptors = flowCodec.linearizeFragment(fragment.proposal);
				} catch (error) {
					linearError = String(error.message || error);
				}
				if (linearError) {
					fragmentPreview.push(jsx("div", { key: "err", style: { color: "#b91c1c" }, children: linearError }));
				} else {
					let at = nodes.length - 1;
					for (let i = 0; i < nodes.length; i += 1) {
						if (nodes[i].id === (primary && primary.id)) at = i + 1;
					}
					if (at >= nodes.length) at = nodes.length - 1;
					fragmentPreview.push(jsx("div", { key: "at", style: { opacity: 0.7 }, children: fill(t("fragment.insertAt"), { index: at }) }));
					for (let i = 0; i < descriptors.length; i += 1) {
						const descriptor = descriptors[i];
						const summary = [];
						const keys = Object.keys(descriptor.params || {});
						for (let k = 0; k < keys.length; k += 1) {
							const value = descriptor.params[keys[k]];
							if (value === "" || value === null || value === undefined) continue;
							if (keys[k] === "extra" || keys[k] === "step") continue;
							summary.push(keys[k] + "=" + (typeof value === "object" ? JSON.stringify(value) : String(value)));
						}
						fragmentPreview.push(jsx("div", {
							key: "draft-" + i,
							"data-lingdong-fragment-step": descriptor.kind,
							style: { padding: "2px 0", borderBottom: "1px solid #e2e8f0" },
							children: (i + 1) + ". " + labelOf(descriptor.kind) + (summary.length ? "  " + summary.join(" ") : "")
						}));
					}
				}
			}

			const fragmentPanel = fragment.open ? jsxs("div", {
				key: "fragment",
				"data-lingdong-fragment": fragment.state,
				style: {
					padding: "6px 8px",
					borderBottom: "1px solid #e2e8f0",
					background: "#fffbeb",
					display: "flex",
					flexDirection: "column",
					gap: "4px",
					maxHeight: "46%",
					overflowY: "auto"
				},
				children: [
					jsxs("div", { key: "head", style: { display: "flex", alignItems: "center", gap: "6px" }, children: [
						jsx("strong", { key: "t", children: t("fragment.title") }),
						jsx("button", { key: "c", type: "button", onClick: closeFragment, style: { marginLeft: "auto" }, children: t("fragment.close") })
					] }),
					jsx("div", { key: "hint", style: { opacity: 0.75 }, children: t("fragment.hint") }),
					jsxs("div", { key: "row", style: { display: "flex", gap: "4px" }, children: [
						jsx("input", {
							value: fragment.goal,
							"data-lingdong-field": "fragmentGoal",
							placeholder: t("fragment.placeholder"),
							onChange: (event) => setFragment((previous) => Object.assign({}, previous, { goal: event.target.value })),
							style: { flex: "1" }
						}),
						jsx("button", {
							key: "g",
							type: "button",
							onClick: generateFragment,
							disabled: !fragment.goal.trim() || fragment.state === "generating",
							"data-lingdong-fragment-generate": "1",
							children: fragment.state === "generating" ? t("fragment.generating") : t("fragment.generate")
						})
					] }),
					fragment.error ? jsx("div", { key: "err", "data-lingdong-fragment-error": "1", style: { color: "#b91c1c" }, children: t("fragment.failed") + "：" + fragment.error }) : null,
					fragment.proposal ? jsx("div", { key: "summary", style: { fontWeight: "bold" }, children: fragment.proposal.summary }) : null,
					fragment.proposal ? jsx("div", { key: "previewTitle", style: { opacity: 0.7 }, children: t("fragment.preview") }) : null,
					fragment.proposal ? jsx("div", { key: "preview", children: fragmentPreview }) : null,
					fragment.calls != null ? jsx("div", { key: "usage", "data-lingdong-fragment-usage": String(fragment.calls), style: { opacity: 0.7 }, children: fill(t("fragment.usage"), { calls: fragment.calls }) }) : null,
					fragment.proposal ? jsxs("div", { key: "actions", style: { display: "flex", gap: "4px" }, children: [
						jsx("button", { key: "apply", type: "button", onClick: applyFragment, "data-lingdong-fragment-apply": "1", children: t("fragment.apply") }),
						jsx("button", { key: "discard", type: "button", onClick: () => setFragment((previous) => Object.assign({}, previous, { proposal: null, state: "idle" })), children: t("fragment.discard") })
					] }) : null
				]
			}) : null;

			const phase = run.state;
			const busy = phase === "validating" || phase === "submitting" || phase === "running";
			const title = doc.name || (doc.file ? doc.file : t("status.untitled"));
			const contentWidth = NODE_W + COLUMN_X * 2;
			const contentHeight = ROW_TOP * 2 + (nodes.length + 1) * ROW_GAP;

			const statusParts = [
				nodes.length + " " + t("status.nodes") + " / " + Math.max(0, nodes.length - 1) + " " + t("status.edges"),
				t("status.modelCalls"),
				doc.file || t("status.unsaved")
			];
			if (dirty) statusParts.push(t("status.dirty"));
			if (selection.length) statusParts.push(fill(t("status.selection"), { count: selection.length }));
			if (fragment.calls != null) statusParts.push(fill(t("status.fragmentCalls"), { calls: fragment.calls }));
			statusParts.push(runSummary(run));

			return jsxs("div", {
				"data-lingdong-flow-workbench": "1",
				tabIndex: 0,
				onKeyDown: onKeyDown,
				style: { display: "flex", flexDirection: "column", height: "100%", fontSize: "12px", background: "#f8fafc", outline: "none" },
				children: [
					jsxs("div", {
						key: "toolbar",
						style: { display: "flex", flexWrap: "wrap", gap: "4px", padding: "6px 8px", borderBottom: "1px solid #e2e8f0", background: "#ffffff" },
						children: [
							jsx("button", { key: "new", type: "button", onClick: newFlow, children: t("toolbar.new") }),
							jsx("button", { key: "save", type: "button", onClick: saveFlow, "data-lingdong-save": "1", children: doc.file ? t("toolbar.save") : t("toolbar.saveAs") }),
							jsx("button", { key: "undo", type: "button", onClick: undo, disabled: !history.past.length, "data-lingdong-undo": "1", children: t("toolbar.undo") }),
							jsx("button", { key: "redo", type: "button", onClick: redo, disabled: !history.future.length, "data-lingdong-redo": "1", children: t("toolbar.redo") }),
							jsx("button", { key: "run", type: "button", onClick: runFlow, disabled: busy, "data-lingdong-run": "1", children: t("toolbar.run") }),
							jsx("button", { key: "stop", type: "button", onClick: stopRun, disabled: !busy, children: t("toolbar.stop") }),
							jsx("button", {
								key: "frag",
								type: "button",
								onClick: () => setFragment((previous) => Object.assign({}, previous, { open: !previous.open })),
								"data-lingdong-fragment-open": "1",
								children: t("toolbar.fragment")
							}),
							jsx("button", { key: "refresh", type: "button", onClick: refreshLibrary, children: t("toolbar.refresh") }),
							jsx("button", { key: "zo", type: "button", onClick: () => zoomBy(1 / 1.2), children: t("toolbar.zoomOut") }),
							jsx("button", { key: "zi", type: "button", onClick: () => zoomBy(1.2), children: t("toolbar.zoomIn") }),
							jsx("button", { key: "zr", type: "button", onClick: () => setView({ scale: 1 }), children: t("toolbar.reset") }),
							jsx("button", { key: "del", type: "button", onClick: deleteSelected, disabled: !selection.length, children: t("toolbar.delete") })
						]
					}),
					fragmentPanel,
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
									jsx("div", { key: "lc", style: { opacity: 0.6, marginBottom: "4px" }, children: library.phase === "loading" ? t("library.loading") : fill(t("library.count"), { count: library.flows.length }) }),
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
								ref: canvasRef,
								onPointerMove: (event) => { moveDrag(event); moveBand(event); },
								onPointerUp: () => { endDrag(); endBand(); },
								onPointerLeave: () => { endDrag(); endBand(); },
								onPointerDown: startBand,
								"data-lingdong-canvas": "1",
								style: { position: "relative", flex: "1", overflow: "auto", background: "#f1f5f9" },
								children: jsx("div", {
									style: { position: "relative", width: contentWidth * view.scale + "px", height: contentHeight * view.scale + "px" },
									children: jsxs("div", {
										style: {
											position: "absolute",
											left: "0",
											top: "0",
											width: contentWidth + "px",
											height: contentHeight + "px",
											transform: "scale(" + view.scale + ")",
											transformOrigin: "0 0"
										},
										children: [
											jsx("svg", {
												key: "edges",
												width: contentWidth,
												height: contentHeight,
												style: { position: "absolute", left: "0", top: "0", pointerEvents: "none" },
												children: edgeShapes
											}),
											jsx("div", { key: "nodes", children: nodeBoxes }),
											band ? jsx("div", {
												key: "band",
												"data-lingdong-band": "1",
												style: {
													position: "absolute",
													left: Math.min(band.x0, band.x1) + "px",
													top: Math.min(band.y0, band.y1) + "px",
													width: Math.abs(band.x1 - band.x0) + "px",
													height: Math.abs(band.y1 - band.y0) + "px",
													border: "1px dashed #2563eb",
													background: "rgba(37,99,235,0.08)",
													pointerEvents: "none"
												}
											}) : null
										]
									})
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
					jsx("div", {
						key: "status",
						"data-lingdong-status": "1",
						"data-lingdong-run-state": phase,
						"data-lingdong-undo-depth": String(history.past.length),
						style: { padding: "4px 8px", borderTop: "1px solid #e2e8f0", background: "#ffffff", opacity: 0.85 },
						children: statusParts.join(" · ")
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
