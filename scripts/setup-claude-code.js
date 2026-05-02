#!/usr/bin/env node
/**
 * Claude Code 集成配置工具
 *
 * 用法：
 *   node scripts/setup-claude-code.js                   查看当前配置建议
 *   node scripts/setup-claude-code.js --apply            写入 claude_desktop_config.json
 *   node scripts/setup-claude-code.js --project          生成 .claude/settings.json MCP 配置
 *
 * 这个脚本帮助你把灵动脚本注册为 Claude Code 的 MCP 工具。
 * 配置后，Claude Code 可以直接调用灵动脚本的桌面自动化工具。
 */

const path = require('path');
const fs = require('fs');

const PROJECT_ROOT = path.resolve(__dirname, '..');
const SERVER_SCRIPT = path.join(PROJECT_ROOT, 'dist', 'ai-interface', 'index.js');

function getMCPConfig() {
  return {
    mcpServers: {
      'lingdong-script': {
        command: 'node',
        args: [SERVER_SCRIPT, '--mode', 'mcp'],
        // 如果使用 HTTP 模式（供其他 AI 工具调用），改成:
        // args: [SERVER_SCRIPT, '--mode', 'http', '--port', '3456'],
        env: {
          DEEPSEEK_API_KEY: '',  // 在这里设置 API key，或通过系统环境变量
        },
      },
    },
  };
}

function getProjectMCPConfig() {
  return {
    mcpServers: {
      'lingdong-script': {
        command: 'node',
        args: [SERVER_SCRIPT, '--mode', 'mcp'],
      },
    },
  };
}

console.log(`
╔══════════════════════════════════════════════════════╗
║        灵动脚本 — Claude Code MCP 集成              ║
╚══════════════════════════════════════════════════════╝

📂 项目路径: ${PROJECT_ROOT}
🚀 服务脚本: ${SERVER_SCRIPT}

────────────────────────────────────────────────────────
🔧 配置方式 1: 全局配置 (~/claude_desktop_config.json)

将以下内容添加到 ~/claude_desktop_config.json:
────────────────────────────────────────────────────────
${JSON.stringify(getMCPConfig(), null, 2)}
────────────────────────────────────────────────────────

🔧 配置方式 2: 项目级配置 (.claude/settings.json)

在项目根目录创建 .claude/settings.json:
────────────────────────────────────────────────────────
${JSON.stringify(getProjectMCPConfig(), null, 2)}
────────────────────────────────────────────────────────

🔧 配置方式 3: 直接 HTTP 调用

任何 AI 工具（ChatGPT、Cursor 等）可以直接调用 HTTP API:
  curl -X POST http://localhost:3456/v1/tools/run_flow \\
    -H "Content-Type: application/json" \\
    -d '{"arguments":{"pseudocode":"按键 win+r\\n等待 0.5\\n输入 \\"notepad\\"\\n按键 enter"}}'

────────────────────────────────────────────────────────
📋 已注册工具:
  • run_flow     — 执行 .flow 脚本或内联伪代码 (优先级: 高)
  • list_flows   — 列出目录下的脚本 (优先级: 中)
  • create_flow  — 创建新的 .flow 脚本 (优先级: 中)
  • ask_ai       — 自然语言→AI自动生成并执行 (优先级: 高)

🌐 提示词开关 (POST /v1/prompt/switch):
  切换 AI 对工具的优先使用程度
  • strong — AI 优先使用灵动脚本完成所有桌面操作
  • medium — AI 在适当时使用
  • light  — AI 仅在明确请求时使用

💡 提示词开关使用:
  curl -X POST http://localhost:3456/v1/prompt/switch \\
    -H "Content-Type: application/json" \\
    -d '{"level":"strong"}'
`);

// --apply 参数: 写入全局配置
if (process.argv.includes('--apply')) {
  const configPath = path.join(require('os').homedir(), 'claude_desktop_config.json');
  let existing = {};
  if (fs.existsSync(configPath)) {
    existing = JSON.parse(fs.readFileSync(configPath, 'utf-8'));
  }
  const newConfig = {
    ...existing,
    ...getMCPConfig(),
  };
  // 合并 mcpServers
  if (existing.mcpServers) {
    newConfig.mcpServers = {
      ...existing.mcpServers,
      ...getMCPConfig().mcpServers,
    };
  }
  fs.writeFileSync(configPath, JSON.stringify(newConfig, null, 2), 'utf-8');
  console.log(`✅ 已写入全局配置: ${configPath}`);
}

// --project 参数: 写入项目级配置
if (process.argv.includes('--project')) {
  const claudeDir = path.join(PROJECT_ROOT, '.claude');
  const settingsPath = path.join(claudeDir, 'settings.json');
  if (!fs.existsSync(claudeDir)) fs.mkdirSync(claudeDir, { recursive: true });

  let existing = {};
  if (fs.existsSync(settingsPath)) {
    existing = JSON.parse(fs.readFileSync(settingsPath, 'utf-8'));
  }
  const newConfig = {
    ...existing,
    ...getProjectMCPConfig(),
  };
  if (existing.mcpServers) {
    newConfig.mcpServers = {
      ...existing.mcpServers,
      ...getProjectMCPConfig().mcpServers,
    };
  }
  fs.writeFileSync(settingsPath, JSON.stringify(newConfig, null, 2), 'utf-8');
  console.log(`✅ 已写入项目级配置: ${settingsPath}`);
}
