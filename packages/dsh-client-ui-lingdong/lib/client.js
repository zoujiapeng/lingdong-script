/**
 * Lingdong flow workbench - DSH Web GUI client plugin.
 *
 * Bundle format: DSH loads client plugins through window.__ModuleLoader__.load,
 * which hands the factory a require() resolving host modules. That wrapper is
 * the entire artifact, so this file is written by hand and no bundler is in
 * the loop (matching how DSH ships its own client bundles).
 *
 * Slice 2 replaces the placeholder with the first real part of the workbench:
 * a flowchart canvas with node palette, node dragging, edge rendering, zoom
 * and selection. The node vocabulary deliberately holds only deterministic,
 * offline-executable steps plus the recorded "script fragment" node - the
 * agent-semantic nodes are not part of this surface.
 */
window.__ModuleLoader__.load({
	id: "@lingdong/dsh-client-ui-lingdong",
	factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;
		Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
		const react = require("react");
		const jsxRuntime = require("react/jsx-runtime");
		const jsx = jsxRuntime.jsx;
		const jsxs = jsxRuntime.jsxs;

		const NS = "@lingdong/dsh-client-ui-lingdong";
		const TAB_ID = "@lingdong/dsh-client-ui-lingdong";
		const TAB_KIND = "lingdong-flow";
		const ADDRESS_PREFIX = "dsh-resource://lingdong-flow/";

		/** Client services this plugin consumes. */
		const inject = ["slots", "locale", "sidebarRightTabs"];

		const dictionaries = {
			zh: {
				"tab.title": "流程工作台",
				"toolbar.add": "添加节点",
				"toolbar.delete": "删除选中",
				"toolbar.zoomIn": "放大",
				"toolbar.zoomOut": "缩小",
				"toolbar.reset": "重置视图",
				"palette.title": "节点库",
				"inspector.title": "属性",
				"inspector.empty": "选中一个节点后在此编辑。",
				"inspector.label": "名称",
				"inspector.kind": "类型",
				"inspector.position": "位置",
				"status.nodes": "节点",
				"status.edges": "连线",
				"status.modelCalls": "模型调用"
			},
			en: {
				"tab.title": "Flow Studio",
				"toolbar.add": "Add node",
				"toolbar.delete": "Delete selected",
				"toolbar.zoomIn": "Zoom in",
				"toolbar.zoomOut": "Zoom out",
				"toolbar.reset": "Reset view",
				"palette.title": "Nodes",
				"inspector.title": "Properties",
				"inspector.empty": "Select a node to edit it here.",
				"inspector.label": "Name",
				"inspector.kind": "Type",
				"inspector.position": "Position",
				"status.nodes": "nodes",
				"status.edges": "edges",
				"status.modelCalls": "model calls"
			}
		};

		/**
		 * Deterministic, offline-executable node vocabulary.
		 * The recorded fragment (replay_step) is the bridge from the agent: one
		 * recorded sequence becomes one ordinary node in the same graph.
		 */
		const PALETTE = [
			{ kind: "click", label: "点击", color: "#0369a1" },
			{ kind: "input_text", label: "输入文本", color: "#0369a1" },
			{ kind: "keyboard", label: "键盘", color: "#0369a1" },
			{ kind: "wait", label: "等待", color: "#475569" },
			{ kind: "condition", label: "条件", color: "#7c3aed" },
			{ kind: "screenshot", label: "截图", color: "#15803d" },
			{ kind: "replay_step", label: "离线脚本片段", color: "#b45309" },
			{ kind: "run_command", label: "运行命令", color: "#b45309" }
		];

		const NODE_W = 148;
		const NODE_H = 46;

		function colorOf(kind) {
			for (let i = 0; i < PALETTE.length; i += 1) {
				if (PALETTE[i].kind === kind) return PALETTE[i].color;
			}
			return "#475569";
		}

		function labelOf(kind) {
			for (let i = 0; i < PALETTE.length; i += 1) {
				if (PALETTE[i].kind === kind) return PALETTE[i].label;
			}
			return kind;
		}

		const INITIAL_NODES = [
			{ id: "start", kind: "start", label: "开始", x: 40, y: 150 },
			{ id: "n1", kind: "click", label: "点击", x: 240, y: 60 },
			{ id: "n2", kind: "replay_step", label: "离线脚本片段", x: 240, y: 240 },
			{ id: "end", kind: "end", label: "结束", x: 460, y: 150 }
		];

		const INITIAL_EDGES = [
			{ id: "e1", from: "start", to: "n1" },
			{ id: "e2", from: "n1", to: "end" },
			{ id: "e3", from: "start", to: "n2" },
			{ id: "e4", from: "n2", to: "end" }
		];

		/** Panel title seat. */
		function LingdongFlowTitle() {
			return jsx("span", { children: dictionaries.zh["tab.title"] });
		}

		/** Centre of a node, in flow coordinates. */
		function centerOf(node) {
			return { x: node.x + NODE_W / 2, y: node.y + NODE_H / 2 };
		}

		function edgePath(from, to) {
			const a = centerOf(from);
			const b = centerOf(to);
			const mid = (a.x + b.x) / 2;
			return "M " + a.x + " " + a.y + " C " + mid + " " + a.y + ", " + mid + " " + b.y + ", " + b.x + " " + b.y;
		}

		/**
		 * The flow workbench pane: canvas, palette, inspector and status bar.
		 * Kept dependency-free so it can be server-rendered for verification and
		 * then mounted unchanged in the browser.
		 */
		function LingdongFlowPane() {
			const nodeState = react.useState(INITIAL_NODES);
			const nodes = nodeState[0];
			const setNodes = nodeState[1];
			const edgeState = react.useState(INITIAL_EDGES);
			const edges = edgeState[0];
			const selectedState = react.useState("");
			const selectedId = selectedState[0];
			const setSelectedId = selectedState[1];
			const viewState = react.useState({ scale: 1, x: 0, y: 0 });
			const view = viewState[0];
			const setView = viewState[1];
			const dragState = react.useState(null);
			const drag = dragState[0];
			const setDrag = dragState[1];

			function selectedNode() {
				for (let i = 0; i < nodes.length; i += 1) {
					if (nodes[i].id === selectedId) return nodes[i];
				}
				return null;
			}

			function addNode(kind) {
				const id = "n" + (nodes.length + 1) + "-" + Math.random().toString(36).slice(2, 6);
				const next = nodes.concat([{
					id: id,
					kind: kind,
					label: labelOf(kind),
					x: 200 + nodes.length * 12,
					y: 330
				}]);
				setNodes(next);
				setSelectedId(id);
			}

			function deleteSelected() {
				if (!selectedId) return;
				const nextNodes = [];
				for (let i = 0; i < nodes.length; i += 1) {
					if (nodes[i].id !== selectedId) nextNodes.push(nodes[i]);
				}
				const nextEdges = [];
				for (let i = 0; i < edges.length; i += 1) {
					if (edges[i].from !== selectedId && edges[i].to !== selectedId) nextEdges.push(edges[i]);
				}
				setNodes(nextNodes);
				setSelectedId("");
			}

			function renameSelected(value) {
				const next = [];
				for (let i = 0; i < nodes.length; i += 1) {
					next.push(nodes[i].id === selectedId ? Object.assign({}, nodes[i], { label: value }) : nodes[i]);
				}
				setNodes(next);
			}

			function zoomBy(factor) {
				setView(Object.assign({}, view, { scale: Math.min(2.5, Math.max(0.4, view.scale * factor)) }));
			}

			function startDrag(event, node) {
				if (event && event.stopPropagation) event.stopPropagation();
				setSelectedId(node.id);
				setDrag({
					id: node.id,
					originX: node.x,
					originY: node.y,
					fromX: event && typeof event.clientX === "number" ? event.clientX : 0,
					fromY: event && typeof event.clientY === "number" ? event.clientY : 0
				});
			}

			function moveDrag(event) {
				if (!drag || !event || typeof event.clientX !== "number") return;
				const next = [];
				for (let i = 0; i < nodes.length; i += 1) {
					if (nodes[i].id !== drag.id) { next.push(nodes[i]); continue; }
					next.push(Object.assign({}, nodes[i], {
						x: drag.originX + (event.clientX - drag.fromX),
						y: drag.originY + (event.clientY - drag.fromY)
					}));
				}
				setNodes(next);
			}

			const byId = {};
			for (let i = 0; i < nodes.length; i += 1) byId[nodes[i].id] = nodes[i];

			const edgeShapes = [];
			for (let i = 0; i < edges.length; i += 1) {
				const from = byId[edges[i].from];
				const to = byId[edges[i].to];
				if (from && to) {
					edgeShapes.push(jsx("path", {
						key: edges[i].id,
						d: edgePath(from, to),
						fill: "none",
						stroke: "#94a3b8",
						strokeWidth: 2
					}));
				}
			}

			const nodeBoxes = [];
			for (let i = 0; i < nodes.length; i += 1) {
				const node = nodes[i];
				const isSelected = node.id === selectedId;
				nodeBoxes.push(jsxs("div", {
					key: node.id,
					"data-lingdong-node": node.kind,
					onPointerDown: (event) => startDrag(event, node),
					style: {
						position: "absolute",
						left: node.x + "px",
						top: node.y + "px",
						width: NODE_W + "px",
						height: NODE_H + "px",
						borderRadius: "8px",
						border: isSelected ? "2px solid #2563eb" : "1px solid #cbd5e1",
						borderLeft: "4px solid " + colorOf(node.kind),
						background: "#ffffff",
						display: "flex",
						alignItems: "center",
						padding: "0 10px",
						fontSize: "12px",
						cursor: "grab",
						boxSizing: "border-box"
					},
					children: jsx("span", { children: node.label })
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
					children: entry.label
				}));
			}

			const current = selectedNode();
			const inspectorChildren = current
				? [
					jsx("div", { key: "k", children: dictionaries.zh["inspector.kind"] + ": " + labelOf(current.kind) }),
					jsxs("label", { key: "l", children: [
						dictionaries.zh["inspector.label"],
						jsx("input", {
							value: current.label,
							onChange: (event) => renameSelected(event.target.value),
							style: { width: "100%", marginTop: "2px" }
						})
					] }),
					jsx("div", { key: "p", children: dictionaries.zh["inspector.position"] + ": " + Math.round(current.x) + ", " + Math.round(current.y) })
				]
				: [jsx("span", { key: "empty", style: { opacity: 0.6 }, children: dictionaries.zh["inspector.empty"] })];

			return jsxs("div", {
				"data-lingdong-flow-workbench": "1",
				style: { display: "flex", flexDirection: "column", height: "100%", fontSize: "12px", background: "#f8fafc" },
				children: [
					jsxs("div", {
						key: "toolbar",
						style: { display: "flex", gap: "6px", padding: "6px 8px", borderBottom: "1px solid #e2e8f0", background: "#ffffff" },
						children: [
							jsx("button", { key: "zi", type: "button", onClick: () => zoomBy(1.2), children: dictionaries.zh["toolbar.zoomIn"] }),
							jsx("button", { key: "zo", type: "button", onClick: () => zoomBy(1 / 1.2), children: dictionaries.zh["toolbar.zoomOut"] }),
							jsx("button", { key: "zr", type: "button", onClick: () => setView({ scale: 1, x: 0, y: 0 }), children: dictionaries.zh["toolbar.reset"] }),
							jsx("button", { key: "del", type: "button", onClick: deleteSelected, disabled: !selectedId, children: dictionaries.zh["toolbar.delete"] })
						]
					}),
					jsxs("div", {
						key: "body",
						style: { display: "flex", flex: "1", minHeight: "0" },
						children: [
							jsxs("div", {
								key: "palette",
								style: { width: "132px", padding: "8px", borderRight: "1px solid #e2e8f0", background: "#ffffff", overflowY: "auto" },
								children: [jsx("strong", { key: "t", children: dictionaries.zh["palette.title"] }), jsx("div", { key: "l", children: paletteButtons })]
							}),
							jsx("div", {
								key: "canvas",
								onPointerMove: moveDrag,
								onPointerUp: () => setDrag(null),
								onPointerDown: () => setSelectedId(""),
								"data-lingdong-canvas": "1",
								style: { position: "relative", flex: "1", overflow: "hidden", background: "#f1f5f9" },
								children: jsxs("div", {
									style: {
										position: "absolute",
										left: "0",
										top: "0",
										transform: "translate(" + view.x + "px," + view.y + "px) scale(" + view.scale + ")",
										transformOrigin: "0 0",
										width: "1200px",
										height: "800px"
									},
									children: [
										jsx("svg", { key: "edges", width: "1200", height: "800", style: { position: "absolute", left: "0", top: "0", pointerEvents: "none" }, children: edgeShapes }),
										jsx("div", { key: "nodes", children: nodeBoxes })
									]
								})
							}),
							jsxs("div", {
								key: "inspector",
								style: { width: "170px", padding: "8px", borderLeft: "1px solid #e2e8f0", background: "#ffffff", overflowY: "auto" },
								children: [jsx("strong", { key: "t", children: dictionaries.zh["inspector.title"] }), jsxs("div", { key: "b", children: inspectorChildren })]
							})
						]
					}),
					jsx("div", {
						key: "status",
						"data-lingdong-status": "1",
						style: { padding: "4px 8px", borderTop: "1px solid #e2e8f0", background: "#ffffff", opacity: 0.75 },
						children: nodes.length + " " + dictionaries.zh["status.nodes"] + " / " + edges.length + " " + dictionaries.zh["status.edges"] + " / 0 " + dictionaries.zh["status.modelCalls"]
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
		return module.exports;
	}
});
