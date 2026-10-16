/**
 * Lingdong flow workbench - DSH Web GUI client plugin.
 *
 * DSH client plugins are loaded through window.__ModuleLoader__.load, which
 * hands the factory a require() resolving host modules (react and the client UI
 * kits). That wrapper is the whole bundle format, so this file is written by
 * hand and needs no bundler in the loop.
 *
 * This first slice registers the tab type and the pane seats for the flow
 * workbench. The canvas itself is migrated next; the panel currently renders a
 * truthful placeholder so the registration path can be verified on its own.
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

		const dictionaries = {
			zh: {
				"tab.title": "流程工作台",
				"panel.heading": "流程工作台",
				"panel.placeholder": "画布迁移中：当前切片只验证 DSH 客户端插件的注册与渲染路径。",
				"panel.hint": "既有实现见 packages/dsh-plugin-lingdong 与 src/renderer。"
			},
			en: {
				"tab.title": "Flow Studio",
				"panel.heading": "Flow Studio",
				"panel.placeholder": "Canvas migration in progress: this slice verifies the DSH client-plugin registration and render path.",
				"panel.hint": "The existing implementation lives in packages/dsh-plugin-lingdong and src/renderer."
			}
		};

		const inject = ["slots", "locale", "sidebarRightTabs"];

		/** Panel title seat. */
		function LingdongFlowTitle() {
			return jsx("span", { children: dictionaries.zh["tab.title"] });
		}

		/** Panel body seat. */
		function LingdongFlowPane() {
			return jsxs("div", {
				style: {
					display: "flex",
					flexDirection: "column",
					gap: "8px",
					padding: "12px",
					fontSize: "12px",
					lineHeight: "1.6"
				},
				"data-lingdong-flow-pane": "1",
				children: [
					jsx("strong", { children: dictionaries.zh["panel.heading"] }),
					jsx("span", { children: dictionaries.zh["panel.placeholder"] }),
					jsx("span", { style: { opacity: 0.65 }, children: dictionaries.zh["panel.hint"] })
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
		return module.exports;
	}
});
