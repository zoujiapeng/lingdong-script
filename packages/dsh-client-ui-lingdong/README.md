# @lingdong/dsh-client-ui-lingdong

流程工作台在 DSH Web GUI 里的客户端插件。

## 为什么可以手写，不需要打包器

DSH 客户端插件不是普通 npm 入口，而是一个 module-loader 包装（`lib/client.js` 就是最终产物）：

    window.__ModuleLoader__.load({
      id: "@lingdong/dsh-client-ui-lingdong",
      factory: (require) => {
        const react = require("react")                 // 宿主注入
        const { jsx } = require("react/jsx-runtime")
        // ctx.slots.register(...) / ctx.locale.register(...)
        exports.apply = apply
        exports.inject = inject
        return module.exports
      }
    })

`require` 由宿主提供，所以不需要 tsdown 或任何打包器；配合 `pnpm run dev:web` 的 HMR 接收器改完即生效。

## 当前切片

| 部分 | 状态 |
| --- | --- |
| 包结构 + dsh.client 声明 + 手写 bundle | 完成 |
| locale 字典注册（zh/en） | 完成 |
| sidebarRightTabs 标签类型（dsh-resource://lingdong-flow/**） | 完成 |
| sidebar.right.pane.tab / .title 座位 | 完成 |
| 画布本体（从 src/renderer 迁移） | 下一步 |

## 验证

    node packages/dsh-client-ui-lingdong/scripts/verify-bundle.mjs

它用桩 `window.__ModuleLoader__` 与桩 `require` 求值真实 bundle，断言导出契约，
再用记录型假 ctx 跑 `apply()`，确认四条注册都发生且无异常。
