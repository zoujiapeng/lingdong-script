import React, { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  Archive,
  Bot,
  CheckCircle2,
  ClipboardCheck,
  DatabaseZap,
  FileCheck2,
  FileText,
  Gauge,
  GitBranch,
  Home,
  LayoutGrid,
  Library,
  ListChecks,
  Moon,
  PanelRight,
  Play,
  RotateCcw,
  Save,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  Square,
  Sun,
  UploadCloud,
  ChevronDown,
  Wand2,
  X,
  Zap,
} from 'lucide-react';
import { AgentView } from '../AgentView';
import { FlowCanvas } from '../Canvas/FlowCanvas';
import { NodeLibrary } from '../NodeLibrary/NodeLibrary';
import { PropertyPanel } from '../PropertyPanel/PropertyPanel';
import { ReplayEditorView } from '../ReplayEditorView';
import { SettingsPanel } from '../SettingsPanel';
import { PseudoPanel } from '../PseudoPanel';
import { useStore, loadCustomTemplates } from '../../store';
import { PRESET_TEMPLATE_META } from '../../store/defaults';
import { NODE_DEFINITIONS } from '../../utils/nodeDefinitions';
import type { NodeType, UIState } from '../../types';

type ViewMode = UIState['viewMode'];

const modes: Array<{ mode: ViewMode; label: string; desc: string; icon: React.ElementType }> = [
  { mode: 'overview', label: '总览', desc: '运行态势', icon: Home },
  { mode: 'agent', label: 'Agent', desc: '实时执行', icon: Bot },
  { mode: 'canvas', label: '流程', desc: '可编辑脚本', icon: GitBranch },
  { mode: 'replay', label: 'Replay', desc: '录制复用', icon: RotateCcw },
  { mode: 'templates', label: '模板', desc: '知识资产', icon: Library },
  { mode: 'settings', label: '设置', desc: '连接与安全', icon: Settings },
];

const statusLabels = {
  idle: { label: '就绪', tone: 'neutral' },
  running: { label: '运行中', tone: 'success' },
  paused: { label: '已暂停', tone: 'warning' },
  stopped: { label: '已停止', tone: 'neutral' },
  completed: { label: '已完成', tone: 'success' },
  error: { label: '出错', tone: 'danger' },
} as const;

const nebulaParticles = Array.from({ length: 18 }, (_, index) => ({
  x: (index * 37 + 11) % 100,
  y: (index * 53 + 19) % 100,
  size: 1 + (index % 4) * 0.55,
  delay: -((index * 0.37) % 9),
  drift: 18 + (index % 7) * 4,
  hue: index % 9 === 0 ? 'amber' : index % 5 === 0 ? 'violet' : 'cyan',
}));

function StatusChip({ label, tone = 'neutral' }: { label: string; tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'info' }) {
  return <span className={`wb-chip wb-chip-${tone}`}>{label}</span>;
}

export function NebulaBackdrop() {
  useEffect(() => {
    const root = document.documentElement;
    let frame = 0;
    let quietTimer = 0;

    const setPoint = (x: number, y: number) => {
      root.style.setProperty('--wb-pointer-x', `${x}px`);
      root.style.setProperty('--wb-pointer-y', `${y}px`);
    };

    const wakeLight = () => {
      root.classList.add('wb-motion-live');
      if (quietTimer) {
        window.clearTimeout(quietTimer);
      }
      quietTimer = window.setTimeout(() => {
        root.classList.remove('wb-motion-live');
      }, 900);
    };

    const handlePointerMove = (event: PointerEvent) => {
      if (frame) {
        cancelAnimationFrame(frame);
      }
      frame = requestAnimationFrame(() => {
        setPoint(event.clientX, event.clientY);
        wakeLight();
      });
    };

    setPoint(window.innerWidth * 0.5, window.innerHeight * 0.34);
    window.addEventListener('pointermove', handlePointerMove, { passive: true });
    window.addEventListener('pointerdown', wakeLight, { passive: true });

    return () => {
      if (frame) {
        cancelAnimationFrame(frame);
      }
      if (quietTimer) {
        window.clearTimeout(quietTimer);
      }
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerdown', wakeLight);
      root.style.removeProperty('--wb-pointer-x');
      root.style.removeProperty('--wb-pointer-y');
      root.classList.remove('wb-motion-live');
    };
  }, []);

  return (
    <div className="wb-nebula" aria-hidden="true">
      <div className="wb-pointer-aura" />
      <div className="wb-siri-lightfield">
        <span className="wb-siri-loop loop-a" />
        <span className="wb-siri-loop loop-b" />
        <span className="wb-siri-loop loop-c" />
      </div>
      <div className="wb-caustic-sheet" />
      <div className="wb-nebula-ribbon ribbon-a" />
      <div className="wb-nebula-ribbon ribbon-b" />
      <div className="wb-nebula-ribbon ribbon-c" />
      <div className="wb-particle-field">
        {nebulaParticles.map((particle, index) => (
          <span
            key={index}
            className={`wb-particle ${particle.hue}`}
            style={{
              '--x': `${particle.x}%`,
              '--y': `${particle.y}%`,
              '--s': `${particle.size}px`,
              '--d': `${particle.delay}s`,
              '--drift': `${particle.drift}px`,
            } as React.CSSProperties}
          />
        ))}
      </div>
    </div>
  );
}

export function SideModeRail() {
  const viewMode = useStore(s => s.ui.viewMode);
  const setViewMode = useStore(s => s.setViewMode);
  const activeMode = viewMode === 'pseudocode' ? 'canvas' : viewMode;

  return (
    <aside className="wb-rail">
      <div className="wb-brand" title="灵动脚本">
        <div className="wb-brand-mark"><Sparkles size={18} /></div>
        <div>
          <strong>灵动</strong>
          <span>Workbench</span>
        </div>
      </div>
      <nav className="wb-mode-list" aria-label="主导航">
        {modes.map(item => {
          const Icon = item.icon;
          const active = activeMode === item.mode;
          return (
            <button
              key={item.mode}
              className={`wb-mode ${active ? 'active' : ''}`}
              onClick={() => setViewMode(item.mode)}
              title={`${item.label} · ${item.desc}`}
            >
              <Icon size={18} />
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>
      <div className="wb-rail-footer">
        <StatusChip label="WinPilot" tone="info" />
        <span>Agent / Flow / Replay 分层运行</span>
      </div>
    </aside>
  );
}

export function CommandDock() {
  const scriptName = useStore(s => s.script.meta.name);
  const isDirty = useStore(s => s.isDirty);
  const executionStatus = useStore(s => s.execution.status);
  const themeMode = useStore(s => s.ui.themeMode);
  const setScriptName = useStore(s => s.setScriptName);
  const setUI = useStore(s => s.setUI);
  const saveScript = useStore(s => s.saveScript);
  const startExecution = useStore(s => s.startExecution);
  const stopExecution = useStore(s => s.stopExecution);
  const autoLayout = useStore(s => s.autoLayout);
  const fitToScreen = useStore(s => s.fitToScreen);
  const [query, setQuery] = useState('');
  const [commandIntent, setCommandIntent] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [titleIntent, setTitleIntent] = useState(false);

  const status = statusLabels[executionStatus] || statusLabels.idle;
  const isRunning = executionStatus === 'running' || executionStatus === 'paused';
  const commandHintsOpen = commandIntent || query.trim().length > 0;
  const toolTabIndex = toolsOpen ? 0 : -1;
  const dockSignals = [
    { label: '模型 OK', tone: 'success' as const },
    { label: 'WP 待连', tone: 'warning' as const },
    { label: '证据', tone: 'info' as const },
    { label: '护栏', tone: 'success' as const },
  ];
  const commandHints = ['运行前体检', '导入最近 trace', '生成验证门'];

  const updateSearch = (value: string) => {
    setQuery(value);
    setUI({ show_unified_search: value.trim().length > 0, unified_search_query: value });
  };

  const closeCommandIntentOnBlur = (event: React.FocusEvent<HTMLDivElement>) => {
    const next = event.relatedTarget as Node | null;
    if (!next || !event.currentTarget.contains(next)) {
      setCommandIntent(false);
    }
  };

  const closeCommandIntentOnPointerLeave = (event: React.PointerEvent<HTMLDivElement>) => {
    const active = document.activeElement;
    if (!active || !event.currentTarget.contains(active)) {
      setCommandIntent(false);
    }
  };

  const closeToolsOnBlur = (event: React.FocusEvent<HTMLDivElement>) => {
    const next = event.relatedTarget as Node | null;
    if (!next || !event.currentTarget.contains(next)) {
      setToolsOpen(false);
    }
  };

  const closeToolsOnPointerLeave = (event: React.PointerEvent<HTMLDivElement>) => {
    const active = document.activeElement;
    if (!active || !event.currentTarget.contains(active)) {
      setToolsOpen(false);
    }
  };

  const closeTitleOnBlur = (event: React.FocusEvent<HTMLDivElement>) => {
    const next = event.relatedTarget as Node | null;
    if (!next || !event.currentTarget.contains(next)) {
      setTitleIntent(false);
    }
  };

  const closeTitleOnPointerLeave = (event: React.PointerEvent<HTMLDivElement>) => {
    const active = document.activeElement;
    if (!active || !event.currentTarget.contains(active)) {
      setTitleIntent(false);
    }
  };

  return (
    <header className="wb-command-dock">
      <div className="wb-command-left">
        <div
          className="wb-command-stack"
          data-hints-open={commandHintsOpen ? 'true' : 'false'}
          onPointerEnter={() => setCommandIntent(true)}
          onPointerLeave={closeCommandIntentOnPointerLeave}
          onFocusCapture={() => setCommandIntent(true)}
          onBlurCapture={closeCommandIntentOnBlur}
        >
          <div className="wb-search wb-liquid-glass">
            <Search size={16} />
            <input
              value={query}
              onChange={e => updateSearch(e.target.value)}
              placeholder="用自然语言搜索或下达命令"
            />
          </div>
          <div className="wb-command-hints">
            {commandHints.map(hint => (
              <button
                key={hint}
                tabIndex={commandHintsOpen ? 0 : -1}
                aria-hidden={!commandHintsOpen}
                onClick={() => updateSearch(hint)}
              >
                {hint}
              </button>
            ))}
          </div>
        </div>
        <div
          className={`wb-script-title wb-liquid-glass${titleIntent ? ' intent' : ''}`}
          tabIndex={0}
          aria-label="Script title"
          onPointerEnter={() => setTitleIntent(true)}
          onPointerLeave={closeTitleOnPointerLeave}
          onFocusCapture={() => setTitleIntent(true)}
          onBlurCapture={closeTitleOnBlur}
        >
          <FileText className="wb-script-title-icon" size={14} />
          {isDirty && <span className="wb-dirty-dot" title="未保存" />}
          <input
            value={scriptName}
            onChange={e => setScriptName(e.target.value)}
            title="脚本名称"
          />
        </div>
        <details className="wb-system-menu">
          <summary title="System status" aria-label="System status">
            <Activity size={14} />
            <ChevronDown size={13} />
          </summary>
          <div className="wb-system-popover wb-liquid-glass">
            {dockSignals.map(signal => (
              <div key={signal.label}>
                <span>{signal.label}</span>
                <StatusChip label={signal.tone === 'success' ? '正常' : signal.tone === 'warning' ? '待连接' : '预备'} tone={signal.tone} />
              </div>
            ))}
          </div>
        </details>
      </div>

      <div className="wb-command-actions">
        <StatusChip label={status.label} tone={status.tone} />
        <div
          className="wb-command-tools"
          data-open={toolsOpen ? 'true' : 'false'}
          tabIndex={0}
          aria-label="Workbench tools"
          aria-expanded={toolsOpen}
          title="Workbench tools"
          onPointerEnter={() => setToolsOpen(true)}
          onPointerLeave={closeToolsOnPointerLeave}
          onFocus={() => setToolsOpen(true)}
          onBlur={closeToolsOnBlur}
          onFocusCapture={() => setToolsOpen(true)}
          onBlurCapture={closeToolsOnBlur}
        >
          <span className="wb-command-tools-handle" aria-hidden="true">
            <Wand2 size={15} />
          </span>
          <button className="wb-icon-btn" tabIndex={toolTabIndex} onClick={() => autoLayout()} title="整理布局">
            <LayoutGrid size={16} />
          </button>
          <button className="wb-icon-btn" tabIndex={toolTabIndex} onClick={fitToScreen} title="适应屏幕">
            <PanelRight size={16} />
          </button>
          <button className="wb-icon-btn" tabIndex={toolTabIndex} onClick={() => setUI({ show_tool_call_panel: true })} title="打开验证与工具结果">
            <ClipboardCheck size={16} />
          </button>
          <button className="wb-icon-btn" tabIndex={toolTabIndex} onClick={saveScript} title="保存">
            <Save size={16} />
          </button>
          <button
            className="wb-icon-btn"
            tabIndex={toolTabIndex}
            onClick={() => {
              const next = themeMode === 'dark' ? 'light' : 'dark';
              setUI({ themeMode: next });
            }}
            title={themeMode === 'dark' ? '切换亮色' : '切换深色'}
          >
            {themeMode === 'dark' ? <Moon size={16} /> : <Sun size={16} />}
          </button>
        </div>
        {isRunning ? (
          <button className="wb-action-btn danger" onClick={stopExecution}>
            <Square size={14} />
            停止
          </button>
        ) : (
          <button className="wb-action-btn success" onClick={startExecution}>
            <Play size={14} />
            运行
          </button>
        )}
      </div>
    </header>
  );
}

export function OverviewView() {
  const setViewMode = useStore(s => s.setViewMode);
  const execution = useStore(s => s.execution);
  const script = useStore(s => s.script);
  const agentHistory = useStore(s => s.agentHistory);
  const triggers = useStore(s => s.triggers);
  const crystallizedTemplates = useStore(s => s.crystallizedTemplates);
  const recentAgent = agentHistory[0];

  const metrics = [
    { label: '流程节点', value: script.nodes.length, helper: `${script.edges.length} 条连接`, tone: 'info' },
    { label: '运行状态', value: statusLabels[execution.status]?.label || '就绪', helper: execution.current_node_id ? '节点执行中' : '等待任务', tone: execution.status === 'error' ? 'danger' : 'success' },
    { label: '最近 Agent', value: recentAgent?.status || '无记录', helper: recentAgent?.run_id || '等待第一次执行', tone: recentAgent?.status === 'fail' ? 'danger' : 'neutral' },
    { label: '结晶模板', value: crystallizedTemplates.length, helper: `${triggers.length} 个触发器`, tone: 'warning' },
  ];
  const readiness = [
    { label: '模型凭证', value: '已就绪', tone: 'success' as const },
    { label: 'WinPilot 服务', value: '按需启动', tone: 'warning' as const },
    { label: '目标窗口', value: '运行时锁定', tone: 'info' as const },
    { label: '完成验证', value: 'done gate', tone: 'success' as const },
  ];

  return (
    <div className="wb-overview wb-scroll">
      <section className="wb-ops-hero wb-liquid-glass">
        <div className="wb-ops-copy">
          <StatusChip label="Mission Control" tone="info" />
          <h1>先确认环境，再让 Agent 动手，最后用证据收口。</h1>
          <p>把模型状态、运行风险、失败恢复和可复用资产放在同一个桌面控制台里。</p>
        </div>
        <div className="wb-ops-actions">
          <button className="wb-action-btn success" onClick={() => setViewMode('agent')}>
            <Bot size={16} />
            发起任务
          </button>
          <button className="wb-action-btn" onClick={() => setViewMode('canvas')}>
            <GitBranch size={16} />
            编排流程
          </button>
          <button className="wb-action-btn" onClick={() => setViewMode('replay')}>
            <RotateCcw size={16} />
            复用记录
          </button>
        </div>
      </section>

      <section className="wb-metric-grid">
        {metrics.map(item => (
          <div key={item.label} className="wb-metric-card wb-liquid-glass">
            <span>{item.label}</span>
            <strong>{item.value}</strong>
            <em>{item.helper}</em>
          </div>
        ))}
      </section>

      <section className="wb-ops-grid">
        <div className="wb-panel wb-readiness-panel wb-liquid-glass">
          <div className="wb-panel-heading">
            <Gauge size={16} />
            <h2>运行体检</h2>
          </div>
          <div className="wb-readiness-stack">
            {readiness.map(item => (
              <div key={item.label} className="wb-readiness-row">
                <span>{item.label}</span>
                <StatusChip label={item.value} tone={item.tone} />
              </div>
            ))}
          </div>
        </div>

        <div className="wb-panel wb-next-panel wb-liquid-glass">
          <div className="wb-panel-heading">
            <Sparkles size={16} />
            <h2>下一步建议</h2>
          </div>
          <div className="wb-next-action">
            <strong>先跑一个低风险 Agent 任务</strong>
            <span>建议限定窗口、要求 before/after 截图，并让 done check 给出文本证据。</span>
            <button className="wb-action-btn success" onClick={() => setViewMode('agent')}>打开任务舱</button>
          </div>
        </div>

        <div className="wb-panel wb-liquid-glass">
          <div className="wb-panel-heading">
            <ListChecks size={16} />
            <h2>待处理失败</h2>
          </div>
          <div className="wb-empty-state">
            <CheckCircle2 size={28} />
            <strong>暂无需要介入的失败</strong>
            <span>失败恢复、重试和证据入口会在这里聚合。</span>
          </div>
        </div>

        <div className="wb-panel wb-liquid-glass">
          <div className="wb-panel-heading">
            <Archive size={16} />
            <h2>最近任务</h2>
          </div>
          {agentHistory.length === 0 ? (
            <div className="wb-empty-state compact">
              <Activity size={24} />
              <strong>还没有 Agent 任务</strong>
              <span>从 Agent Cockpit 发起后会生成复盘卡片。</span>
            </div>
          ) : (
            <div className="wb-list">
              {agentHistory.slice(0, 4).map(item => (
                <div key={item.id} className="wb-list-row">
                  <span>{item.goal || item.run_id || 'Agent run'}</span>
                  <StatusChip label={item.status} tone={item.status === 'fail' ? 'danger' : 'success'} />
                </div>
              ))}
            </div>
          )}
        </div>

        <EvidencePanel />
        <div className="wb-panel wb-crystal-panel wb-liquid-glass">
          <div className="wb-panel-heading">
            <Library size={16} />
            <h2>可复用资产</h2>
          </div>
          <div className="wb-crystal-stack">
            <button onClick={() => setViewMode('templates')}>登录自动化</button>
            <button onClick={() => setViewMode('templates')}>定时截图记录</button>
            <button onClick={() => setViewMode('templates')}>保存文件对话框</button>
          </div>
        </div>
      </section>
    </div>
  );
}

export function FocusOverviewView() {
  const setViewMode = useStore(s => s.setViewMode);
  const execution = useStore(s => s.execution);
  const script = useStore(s => s.script);
  const agentHistory = useStore(s => s.agentHistory);
  const recentAgent = agentHistory[0];
  const status = statusLabels[execution.status] || statusLabels.idle;

  return (
    <div className="wb-overview wb-focus-shell wb-scroll">
      <section className="wb-focus-card wb-liquid-glass">
        <div className="wb-focus-copy">
          <StatusChip label="Focus Workbench" tone="info" />
          <h1>把目标交给 Agent，其余内容先安静退后。</h1>
          <p>当前脚本：{script.meta.name || '未命名任务'}。默认只保留任务入口、运行状态和证据入口；流程、复盘、模板与设置按需展开。</p>
        </div>
        <button className="wb-action-btn success wb-focus-primary" onClick={() => setViewMode('agent')}>
          <Bot size={17} />
          打开任务舱
        </button>
      </section>

      <section className="wb-focus-status" aria-label="当前状态">
        <span>
          <strong>{status.label}</strong>
          运行状态
        </span>
        <span>
          <strong>{script.nodes.length}</strong>
          流程节点
        </span>
        {recentAgent && (
          <button onClick={() => setViewMode('agent')}>
            <strong>{recentAgent.status}</strong>
            最近 Agent
          </button>
        )}
      </section>

      <details className="wb-focus-drawer wb-liquid-glass">
        <summary>
          <span>工具、证据和高级工作区</span>
          <ChevronDown size={15} />
        </summary>
        <div className="wb-focus-tools">
          <button onClick={() => setViewMode('canvas')}>
            <GitBranch size={16} />
            流程编辑
          </button>
          <button onClick={() => setViewMode('replay')}>
            <RotateCcw size={16} />
            复盘 Replay
          </button>
          <button onClick={() => setViewMode('templates')}>
            <Library size={16} />
            模板资产
          </button>
          <button onClick={() => setViewMode('settings')}>
            <Settings size={16} />
            连接设置
          </button>
        </div>
        <EvidencePanel compact />
      </details>
    </div>
  );
}

type RoutedSurface = Extract<ViewMode, 'canvas' | 'pseudocode' | 'replay' | 'settings'>;
type ContextSurface = RoutedSurface;
type LensMode = ContextSurface | 'library';

const routedSurfaces: RoutedSurface[] = ['canvas', 'pseudocode', 'replay', 'settings'];

function getRoutedSurface(mode: ViewMode): RoutedSurface | null {
  return routedSurfaces.includes(mode as RoutedSurface) ? (mode as RoutedSurface) : null;
}

const contextSurfaces: Array<{ mode: LensMode; label: string; detail: string; icon: React.ElementType }> = [
  { mode: 'canvas', label: 'Flow', detail: 'graph', icon: GitBranch },
  { mode: 'replay', label: 'Replay', detail: 'trace', icon: RotateCcw },
  { mode: 'library', label: 'Library', detail: 'playbooks', icon: Library },
  { mode: 'settings', label: 'System', detail: 'runtime', icon: Settings },
];

const surfaceMeta: Record<ContextSurface, { title: string; kicker: string }> = {
  canvas: { title: 'Flow', kicker: 'Graph editor' },
  pseudocode: { title: 'Flow text', kicker: 'Script view' },
  replay: { title: 'Replay', kicker: 'Trace lab' },
  settings: { title: 'System', kicker: 'Runtime' },
};

export function UnifiedWorkbenchView() {
  const viewMode = useStore(s => s.ui.viewMode);
  const setViewMode = useStore(s => s.setViewMode);
  const setUI = useStore(s => s.setUI);
  const execution = useStore(s => s.execution);
  const script = useStore(s => s.script);
  const [localSurface, setLocalSurface] = useState<ContextSurface | null>(null);
  const [railIntent, setRailIntent] = useState<LensMode | null>(null);

  const routedSurface = getRoutedSurface(viewMode);
  const surface = routedSurface || localSurface;
  const status = statusLabels[execution.status] || statusLabels.idle;

  useEffect(() => {
    if (routedSurface) {
      setLocalSurface(null);
    }
    if (viewMode === 'templates') {
      setLocalSurface(null);
      setViewMode('overview');
    }
  }, [routedSurface, setViewMode, viewMode]);

  const openSurface = (next: LensMode) => {
    if (next === 'library') {
      setLocalSurface(null);
      setUI({ panels_visible: false, show_node_library: false, show_property_panel: false });
      setViewMode('overview');
      return;
    }
    if (next === 'canvas' || next === 'pseudocode') {
      setUI({ panels_visible: true, show_node_library: false, show_property_panel: false });
      requestAnimationFrame(() => useStore.getState().fitToScreen());
    }
    setLocalSurface(null);
    setViewMode(next);
  };

  const closeSurface = () => {
    setLocalSurface(null);
    setUI({ panels_visible: false, show_node_library: false, show_property_panel: false });
    setViewMode('overview');
  };

  return (
    <div className={`wb-unified-workbench${surface ? ' context-open' : ''}`} data-surface={surface || 'focus'}>
      <section className="wb-mission-bar wb-liquid-glass" aria-label="Mission status">
        <div className="wb-mission-core">
          <span className={`wb-mission-orb ${execution.status}`} />
          <div>
            <strong>{script.meta.name || 'Untitled mission'}</strong>
            <span>{status.label} · {script.nodes.length} nodes · {script.edges.length} links</span>
          </div>
        </div>

        <div className="wb-mission-pulse" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>

      </section>

      <div className="wb-unified-stage">
        <div className="wb-unified-agent-shell">
          <AgentCockpit />
        </div>

        <nav className="wb-context-switcher wb-liquid-glass" aria-label="Context lenses">
          {contextSurfaces.map(item => {
            const Icon = item.icon;
            const active = item.mode === 'library'
              ? false
              : surface === item.mode || (item.mode === 'canvas' && surface === 'pseudocode');
            const intent = railIntent === item.mode;
            return (
              <button
                key={item.mode}
                type="button"
                className={`${active ? 'active' : ''}${intent ? ' intent' : ''}${item.mode === 'library' ? ' has-bubble' : ''}`}
                onPointerEnter={() => setRailIntent(item.mode)}
                onPointerLeave={() => setRailIntent(current => (current === item.mode ? null : current))}
                onFocus={() => setRailIntent(item.mode)}
                onBlur={() => setRailIntent(current => (current === item.mode ? null : current))}
                onClick={() => openSurface(item.mode)}
                aria-pressed={active}
                title={`${item.label}: ${item.detail}`}
              >
                <Icon size={16} />
                <span>{item.label}</span>
                <em>{item.detail}</em>
                {item.mode === 'library' && <TemplateHoverBubble />}
              </button>
            );
          })}
        </nav>

        <aside className="wb-context-layer" data-open={surface ? 'true' : 'false'} aria-hidden={!surface}>
          {surface && (
            <>
              <div className="wb-context-head">
                <div>
                  <StatusChip label={surfaceMeta[surface].title} tone="info" />
                  <span>{surfaceMeta[surface].kicker}</span>
                </div>
                <button className="wb-icon-btn" type="button" onClick={closeSurface} title="Close context">
                  <X size={16} />
                </button>
              </div>
              <ContextLayerBody surface={surface} />
            </>
          )}
        </aside>
      </div>
    </div>
  );
}

function ContextLayerBody({ surface }: { surface: ContextSurface }) {
  const setViewMode = useStore(s => s.setViewMode);
  const script = useStore(s => s.script);

  if (surface === 'canvas' || surface === 'pseudocode') {
    return (
      <div className="wb-context-body wb-context-flow">
        <div className="wb-flow-context-top wb-flow-context-top-min">
          <div>
            <strong>{script.meta.name}</strong>
            <span>{script.nodes.length} nodes · {script.edges.length} links</span>
          </div>
          <div className="wb-inline-actions wb-flow-icon-actions">
            <button className="wb-icon-btn" type="button" onClick={() => setViewMode(surface === 'canvas' ? 'pseudocode' : 'canvas')} title={surface === 'canvas' ? 'Text view' : 'Graph view'}>
              <FileText size={15} />
            </button>
          </div>
        </div>
        {surface === 'pseudocode' ? (
          <div className="wb-flow-pseudo-context">
            <PseudoPanel />
          </div>
        ) : (
          <FlowComposerLens />
        )}
      </div>
    );
  }

  if (surface === 'replay') {
    return (
      <div className="wb-context-body wb-context-replay">
        <ReplayEditorView />
      </div>
    );
  }

  return (
    <div className="wb-context-body wb-context-settings">
      <SettingsPanel />
    </div>
  );
}

function TemplateHoverBubble() {
  const presets = PRESET_TEMPLATE_META.slice(0, 4);
  const customCount = useMemo(() => loadCustomTemplates().length, []);

  return (
    <span className="wb-library-bubble" aria-hidden="true">
      <strong>Templates</strong>
      <span>{customCount} saved · playbooks</span>
      <span className="wb-library-list">
        {presets.map(template => (
          <i key={template.id}>{template.name}</i>
        ))}
      </span>
    </span>
  );
}

function FlowComposerLens() {
  const script = useStore(s => s.script);
  const selection = useStore(s => s.ui.selection);
  const execution = useStore(s => s.execution);
  const fitToScreen = useStore(s => s.fitToScreen);
  const setUI = useStore(s => s.setUI);
  const [mapPinned, setMapPinned] = useState(false);
  const [libraryIntent, setLibraryIntent] = useState(false);
  const [propertyIntent, setPropertyIntent] = useState(false);
  const [mapIntent, setMapIntent] = useState(false);
  const hasSelection = selection.nodes.length > 0 || selection.edges.length > 0;
  const propertyOpen = hasSelection || propertyIntent;
  const mapOpen = mapPinned || mapIntent;

  const closeIntentOnBlur = (setOpen: React.Dispatch<React.SetStateAction<boolean>>) => (
    event: React.FocusEvent<HTMLElement>,
  ) => {
    const next = event.relatedTarget as Node | null;
    if (!next || !event.currentTarget.contains(next)) {
      setOpen(false);
    }
  };

  const closeIntentOnPointerLeave = (setOpen: React.Dispatch<React.SetStateAction<boolean>>) => (
    event: React.PointerEvent<HTMLElement>,
  ) => {
    const active = document.activeElement;
    if (!active || !event.currentTarget.contains(active)) {
      setOpen(false);
    }
  };

  return (
    <div className={`wb-flow-compose ${hasSelection ? 'has-selection' : ''}${libraryIntent ? ' library-open' : ''}${propertyOpen ? ' property-open' : ''}${mapOpen ? ' map-open' : ''}`}>
      <div className="wb-flow-compose-strip">
        <span><strong>{script.nodes.length}</strong> nodes</span>
        <span><strong>{script.edges.length}</strong> links</span>
        <span><strong>{execution.current_node_id || 'idle'}</strong> current</span>
        <button type="button" onClick={fitToScreen} title="Fit canvas">
          <PanelRight size={14} />
        </button>
      </div>

      <aside
        className="wb-compose-library"
        onPointerEnter={() => setLibraryIntent(true)}
        onPointerLeave={closeIntentOnPointerLeave(setLibraryIntent)}
        onFocusCapture={() => setLibraryIntent(true)}
        onBlurCapture={closeIntentOnBlur(setLibraryIntent)}
      >
        <div className="wb-compose-handle">
          <Library size={15} />
          <span>Nodes</span>
        </div>
        <NodeLibrary />
      </aside>

      <main
        className="wb-compose-canvas"
        onPointerEnter={() => setUI({ show_node_library: false, show_property_panel: false })}
      >
        <FlowCanvas />
      </main>

      <aside
        className="wb-compose-property"
        onPointerEnter={() => setPropertyIntent(true)}
        onPointerLeave={closeIntentOnPointerLeave(setPropertyIntent)}
        onFocusCapture={() => setPropertyIntent(true)}
        onBlurCapture={closeIntentOnBlur(setPropertyIntent)}
      >
        <div className="wb-compose-handle">
          <Wand2 size={15} />
          <span>Params</span>
        </div>
        <PropertyPanel />
      </aside>

      <div
        className={`wb-flow-map-peek${mapPinned ? ' pinned' : ''}${mapIntent ? ' intent' : ''}`}
        onPointerEnter={() => setMapIntent(true)}
        onPointerLeave={closeIntentOnPointerLeave(setMapIntent)}
        onFocusCapture={() => setMapIntent(true)}
        onBlurCapture={closeIntentOnBlur(setMapIntent)}
      >
        <button
          className="wb-flow-map-toggle"
          type="button"
          onPointerEnter={() => setMapIntent(true)}
          onFocus={() => setMapIntent(true)}
          onClick={() => setMapPinned(current => !current)}
          title="Structure peek"
          aria-pressed={mapPinned}
        >
          <GitBranch size={15} />
        </button>
        <CompactFlowGraph />
      </div>
    </div>
  );
}

function CompactFlowGraph() {
  const script = useStore(s => s.script);
  const execution = useStore(s => s.execution);
  const selection = useStore(s => s.ui.selection);
  const selectNodes = useStore(s => s.selectNodes);
  const clearSelection = useStore(s => s.clearSelection);
  const addNode = useStore(s => s.addNode);
  const setViewMode = useStore(s => s.setViewMode);
  const [previewNodeId, setPreviewNodeId] = useState<string | null>(null);

  const layout = useMemo(() => {
    const nodes = script.nodes;
    if (nodes.length === 0) return [];
    const minX = Math.min(...nodes.map(node => node.x));
    const maxX = Math.max(...nodes.map(node => node.x));
    const minY = Math.min(...nodes.map(node => node.y));
    const maxY = Math.max(...nodes.map(node => node.y));
    const spanX = Math.max(1, maxX - minX);
    const spanY = Math.max(1, maxY - minY);
    const useGrid = nodes.length > 18 || (spanX < 60 && spanY < 60);
    const cols = Math.min(8, Math.max(2, Math.ceil(Math.sqrt(nodes.length * 1.35))));

    return nodes.map((node, index) => {
      const gridCol = index % cols;
      const gridRow = Math.floor(index / cols);
      const gridRows = Math.max(1, Math.ceil(nodes.length / cols));
      const x = useGrid ? 10 + (gridCol + 0.5) * (80 / cols) : 8 + ((node.x - minX) / spanX) * 84;
      const y = useGrid ? 18 + (gridRow + 0.5) * (66 / gridRows) : 16 + ((node.y - minY) / spanY) * 68;
      return { node, x, y };
    });
  }, [script.nodes]);

  const pointById = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>();
    layout.forEach(item => map.set(item.node.id, { x: item.x, y: item.y }));
    return map;
  }, [layout]);

  const selectedNode = script.nodes.find(node => selection.nodes.includes(node.id));
  const failureEdges = script.edges.filter(edge => edge.label === 'fail' || edge.label === 'no').length;

  const quickAdd = (type: NodeType) => {
    const x = 180 + script.nodes.length * 28;
    const y = 180 + (script.nodes.length % 4) * 46;
    const id = addNode(type, x, y);
    selectNodes([id]);
  };

  const previewValue = (value: unknown) => {
    if (value === null || value === undefined || value === '') return 'empty';
    if (Array.isArray(value)) return `${value.length} items`;
    if (typeof value === 'object') return 'object';
    return String(value).slice(0, 36);
  };

  return (
    <div className="wb-mini-flow">
      <div className="wb-mini-flow-metrics">
        <span><strong>{script.nodes.length}</strong> nodes</span>
        <span><strong>{script.edges.length}</strong> links</span>
        <span><strong>{failureEdges}</strong> branches</span>
      </div>

      <svg className="wb-mini-flow-wires" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        <defs>
          <linearGradient id="wb-flow-wire" x1="0%" x2="100%" y1="0%" y2="0%">
            <stop offset="0%" stopColor="rgba(34, 211, 238, 0.08)" />
            <stop offset="52%" stopColor="rgba(129, 140, 248, 0.6)" />
            <stop offset="100%" stopColor="rgba(245, 158, 11, 0.44)" />
          </linearGradient>
        </defs>
        {script.edges.map(edge => {
          const from = pointById.get(edge.from_node);
          const to = pointById.get(edge.to_node);
          if (!from || !to) return null;
          const mid = (from.x + to.x) / 2;
          const branch = edge.label === 'fail' || edge.label === 'no';
          return (
            <path
              key={edge.id}
              className={branch ? 'branch' : ''}
              d={`M ${from.x} ${from.y} C ${mid} ${from.y}, ${mid} ${to.y}, ${to.x} ${to.y}`}
            />
          );
        })}
      </svg>

      <div className="wb-mini-flow-nodes" onClick={clearSelection}>
        {layout.map(({ node, x, y }) => {
          const def = NODE_DEFINITIONS[node.type];
          const label = node.label || def?.label || node.type;
          const isSelected = selection.nodes.includes(node.id);
          const isPreview = previewNodeId === node.id;
          const isCurrent = execution.current_node_id === node.id;
          const status = isCurrent ? 'running' : node.status || 'idle';
          const params = Object.entries((node.params || {}) as Record<string, unknown>).slice(0, 4);
          const popupSide = y > 58 ? 'above' : 'below';
          const popupAlign = x < 28 ? 'align-right' : x > 72 ? 'align-left' : 'align-center';
          return (
            <button
              key={node.id}
              type="button"
              className={`wb-mini-flow-node ${node.type} ${status} ${isSelected ? 'selected' : ''}${isPreview ? ' preview' : ''}`}
              style={{ left: `${x}%`, top: `${y}%`, '--node-color': def?.color || '#22d3ee' } as React.CSSProperties}
              onPointerEnter={() => setPreviewNodeId(node.id)}
              onPointerLeave={() => setPreviewNodeId(current => (current === node.id ? null : current))}
              onFocus={() => setPreviewNodeId(node.id)}
              onBlur={() => setPreviewNodeId(current => (current === node.id ? null : current))}
              onClick={event => {
                event.stopPropagation();
                selectNodes([node.id]);
              }}
              title={label}
            >
              <span className="wb-mini-flow-flipper">
                <span className="wb-mini-flow-face wb-mini-flow-front">
                  <span className="wb-mini-flow-dot" />
                  <span className="wb-mini-flow-copy">
                    <strong>{label}</strong>
                    <em>{node.type}</em>
                  </span>
                </span>
                <span className="wb-mini-flow-face wb-mini-flow-back">
                  <b>{status}</b>
                  {params.length === 0 ? (
                    <small>no params</small>
                  ) : params.slice(0, 2).map(([key, value]) => (
                    <small key={key}>{key}: {previewValue(value)}</small>
                  ))}
                </span>
              </span>
              <span className="wb-mini-flow-tip">
                <b>{status}</b>
                {params.length === 0 ? (
                  <small>no params</small>
                ) : params.map(([key, value]) => (
                  <small key={key}>{key}: {previewValue(value)}</small>
                ))}
              </span>
              {isPreview && (
                <span className={`wb-mini-flow-popup ${popupSide} ${popupAlign}`}>
                  <b>{status}</b>
                  {params.length === 0 ? (
                    <small>no params</small>
                  ) : params.map(([key, value]) => (
                    <small key={key}>{key}: {previewValue(value)}</small>
                  ))}
                </span>
              )}
            </button>
          );
        })}
      </div>

      <div className="wb-mini-flow-tools">
        <div className="wb-mini-tool" tabIndex={0} title="Quick add">
          <Zap size={15} />
          <div className="wb-mini-popover">
            <strong>Quick add</strong>
            <button type="button" onClick={() => quickAdd('wait')}>Wait</button>
            <button type="button" onClick={() => quickAdd('click')}>Click</button>
            <button type="button" onClick={() => quickAdd('input_text')}>Input</button>
          </div>
        </div>
        <div className="wb-mini-tool" tabIndex={0} title="Selected node">
          <ListChecks size={15} />
          <div className="wb-mini-popover wide">
            <strong>{selectedNode ? (selectedNode.label || NODE_DEFINITIONS[selectedNode.type]?.label || selectedNode.type) : 'Nothing selected'}</strong>
            <span>{selectedNode ? selectedNode.type : 'No selection'}</span>
          </div>
        </div>
        <button className="wb-mini-tool" type="button" title="Text view" onClick={() => setViewMode('pseudocode')}>
          <FileText size={15} />
        </button>
      </div>
    </div>
  );
}

export function AgentCockpit() {
  const saveAsTemplate = useStore(s => s.saveAsTemplate);
  const [draft, setDraft] = useState('帮我完成一个桌面自动化任务，并在完成后给出证据。');
  const [copied, setCopied] = useState(false);
  const [taskDrawerOpen, setTaskDrawerOpen] = useState(false);
  const [evidenceDrawerOpen, setEvidenceDrawerOpen] = useState(false);
  const taskTabIndex = taskDrawerOpen ? 0 : -1;

  const copyDraft = async () => {
    try {
      await navigator.clipboard?.writeText(draft);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  };

  const closeDrawerOnBlur = (setOpen: React.Dispatch<React.SetStateAction<boolean>>) => (
    event: React.FocusEvent<HTMLElement>,
  ) => {
    const next = event.relatedTarget as Node | null;
    if (!next || !event.currentTarget.contains(next)) {
      setOpen(false);
    }
  };

  const closeDrawerOnPointerLeave = (setOpen: React.Dispatch<React.SetStateAction<boolean>>) => (
    event: React.PointerEvent<HTMLElement>,
  ) => {
    const active = document.activeElement;
    if (!active || !event.currentTarget.contains(active)) {
      setOpen(false);
    }
  };

  return (
    <div className="wb-agent-cockpit">
      <aside
        className="wb-agent-side wb-scroll"
        data-open={taskDrawerOpen ? 'true' : 'false'}
        tabIndex={0}
        aria-label="Task contract drawer"
        aria-expanded={taskDrawerOpen}
        title="Task contract"
        onPointerEnter={() => setTaskDrawerOpen(true)}
        onPointerLeave={closeDrawerOnPointerLeave(setTaskDrawerOpen)}
        onFocusCapture={() => setTaskDrawerOpen(true)}
        onBlurCapture={closeDrawerOnBlur(setTaskDrawerOpen)}
      >
        <div className="wb-panel-heading">
          <Bot size={16} />
          <h2>任务契约</h2>
        </div>
        <details className="wb-contract-disclosure">
          <summary tabIndex={taskTabIndex}>
            <span>目标、边界和成功证据</span>
            <ChevronDown size={14} />
          </summary>
          <div className="wb-contract-stack">
            <div>
              <span>目标</span>
              <strong>让 Agent 完成桌面操作</strong>
            </div>
            <div>
              <span>边界</span>
              <strong>限定窗口，敏感动作确认</strong>
            </div>
            <div>
              <span>成功证据</span>
              <strong>截图 + 文本 + done check</strong>
            </div>
          </div>
        </details>
        <textarea tabIndex={taskTabIndex} value={draft} onChange={e => setDraft(e.target.value)} />
        <div className="wb-inline-actions">
          <button className="wb-action-btn" tabIndex={taskTabIndex} onClick={copyDraft}>
            <UploadCloud size={15} />
            {copied ? '已复制' : '复制'}
          </button>
          <button className="wb-action-btn" tabIndex={taskTabIndex} onClick={() => saveAsTemplate('Agent 任务草稿', draft)}>
            <Sparkles size={15} />
            结晶
          </button>
        </div>

        <div className="wb-timeline">
          {['理解任务', '观察窗口', '执行动作', '验证证据'].map((step, index) => (
            <div key={step} className={`wb-timeline-row ${index === 1 ? 'active' : ''}`}>
              <span>{index + 1}</span>
              <strong>{step}</strong>
              <em>{index === 0 ? '已准备' : index === 1 ? '实时' : '等待'}</em>
            </div>
          ))}
        </div>
      </aside>

      <main className="wb-agent-screen">
        <div className="wb-surface-title">
          <span><Gauge size={16} /> 实时 Agent 屏幕</span>
          <StatusChip label="Live" tone="info" />
        </div>
        <div className="wb-agent-frame">
          <div className="wb-frame-scan" aria-hidden="true" />
          <AgentView />
        </div>
      </main>

      <aside
        className="wb-agent-side wb-scroll"
        data-open={evidenceDrawerOpen ? 'true' : 'false'}
        tabIndex={0}
        aria-label="Evidence and guardrail drawer"
        aria-expanded={evidenceDrawerOpen}
        title="Evidence and guardrails"
        onPointerEnter={() => setEvidenceDrawerOpen(true)}
        onPointerLeave={closeDrawerOnPointerLeave(setEvidenceDrawerOpen)}
        onFocusCapture={() => setEvidenceDrawerOpen(true)}
        onBlurCapture={closeDrawerOnBlur(setEvidenceDrawerOpen)}
      >
        <EvidencePanel compact />
        <div className="wb-panel">
          <div className="wb-panel-heading">
            <ShieldCheck size={16} />
            <h2>安全护栏</h2>
          </div>
          <div className="wb-guard-list">
            <StatusChip label="窗口限定" tone="success" />
            <StatusChip label="文件写入待确认" tone="warning" />
            <StatusChip label="外部命令隔离" tone="info" />
            <StatusChip label="敏感输入遮罩" tone="neutral" />
          </div>
        </div>
      </aside>
    </div>
  );
}

export function FlowStudioLayout() {
  const execution = useStore(s => s.execution);
  const script = useStore(s => s.script);
  const autoLayout = useStore(s => s.autoLayout);
  const setViewMode = useStore(s => s.setViewMode);

  return (
    <div className="wb-flow-studio">
      <div className="wb-studio-top wb-liquid-glass">
        <div>
          <StatusChip label="Automation Studio" tone="info" />
          <h1>{script.meta.name}</h1>
          <p>主路径自上而下，失败恢复向右展开，高风险动作后保留验证门。</p>
        </div>
        <div className="wb-inline-actions">
          <button className="wb-action-btn" onClick={() => setViewMode('pseudocode')}>
            <FileText size={15} />
            伪代码
          </button>
          <button className="wb-action-btn" onClick={() => autoLayout()}>
            <LayoutGrid size={15} />
            整理布局
          </button>
        </div>
      </div>
      <div className="wb-studio-body">
        <aside className="wb-smart-library">
          <div className="wb-panel-heading inset">
            <Search size={16} />
            <h2>搜索式指令库</h2>
          </div>
          <NodeLibrary />
        </aside>
        <main className="wb-canvas-zone">
          <div className="wb-flow-rule-strip">
            <StatusChip label="验证门可见" tone="success" />
            <StatusChip label="失败恢复侧挂" tone="warning" />
            <StatusChip label="最多 1-3 次重试" tone="info" />
          </div>
          <FlowCanvas />
        </main>
        <aside className="wb-smart-property">
          <div className="wb-panel-heading inset">
            <Wand2 size={16} />
            <h2>智能属性</h2>
          </div>
          <PropertyPanel />
        </aside>
      </div>
      <div className="wb-execution-strip">
        <span>当前状态：{statusLabels[execution.status]?.label || execution.status}</span>
        <span>当前节点：{execution.current_node_id || '未运行'}</span>
        <span>日志入口：{execution.log.length} 条</span>
      </div>
    </div>
  );
}

export function PseudoWorkbenchView() {
  const setViewMode = useStore(s => s.setViewMode);
  return (
    <div className="wb-pseudo-shell">
      <div className="wb-studio-top wb-liquid-glass">
        <div>
          <StatusChip label="FlowScript" tone="info" />
          <h1>伪代码视图</h1>
          <p>保留原有文本视图，用于检查流程语义和导出前审阅。</p>
        </div>
        <button className="wb-action-btn" onClick={() => setViewMode('canvas')}>
          <GitBranch size={15} />
          返回流程
        </button>
      </div>
      <div className="wb-pseudo-panel">
        <PseudoPanel />
      </div>
    </div>
  );
}

export function ReplayLab() {
  return (
    <div className="wb-replay-lab wb-scroll">
      <div className="wb-studio-top wb-liquid-glass">
        <div>
          <StatusChip label="Replay Lab" tone="info" />
          <h1>把成功操作变成可复用资产</h1>
          <p>Trace、WPS 原样重放、FlowScript 导入和校准证据分开呈现。</p>
        </div>
      </div>
      <div className="wb-replay-grid">
        <div className="wb-panel">
          <div className="wb-panel-heading">
            <DatabaseZap size={16} />
            <h2>映射说明</h2>
          </div>
          <div className="wb-mapping-list">
            <span>click / input_text / wait → 可编辑节点</span>
            <span>draw_strokes / tray_click → WPS 占位</span>
            <span>原始截图 / 目标 / done check → 证据入口</span>
          </div>
        </div>
        <div className="wb-panel">
          <div className="wb-panel-heading">
            <ShieldCheck size={16} />
            <h2>不可等价步骤</h2>
          </div>
          <div className="wb-guard-list">
            <StatusChip label="draw_strokes" tone="warning" />
            <StatusChip label="run_detached" tone="warning" />
            <StatusChip label="while/until" tone="warning" />
            <StatusChip label="unknown step" tone="danger" />
          </div>
        </div>
      </div>
      <div className="wb-replay-main">
        <ReplayEditorView />
      </div>
    </div>
  );
}

export function TemplateKnowledgeView() {
  const loadTemplate = useStore(s => s.loadTemplate);
  const setViewMode = useStore(s => s.setViewMode);
  const saveAsTemplate = useStore(s => s.saveAsTemplate);
  const customTemplates = useMemo(() => loadCustomTemplates(), []);
  const presets = PRESET_TEMPLATE_META.slice(0, 6);

  const loadPreset = (id: string) => {
    loadTemplate(id);
    setViewMode('canvas');
  };

  return (
    <div className="wb-template-view wb-scroll">
      <div className="wb-studio-top wb-liquid-glass">
        <div>
          <StatusChip label="Template & Knowledge" tone="info" />
          <h1>把脚本、演示和成功证据沉淀成模板</h1>
          <p>预设模板、我的模板、最近结晶和已验证资产集中管理。</p>
        </div>
        <button className="wb-action-btn success" onClick={() => saveAsTemplate('当前流程模板')}>
          <Sparkles size={15} />
          保存当前
        </button>
      </div>
      <section className="wb-template-grid">
        {presets.map(template => (
          <button key={template.id} className="wb-template-card wb-liquid-glass" onClick={() => loadPreset(template.id)}>
            <FileCheck2 size={18} />
            <strong>{template.name}</strong>
            <span>{template.description}</span>
            <em>{template.tags.join(' · ')}</em>
          </button>
        ))}
      </section>
      <section className="wb-panel">
        <div className="wb-panel-heading">
          <Library size={16} />
          <h2>我的模板</h2>
        </div>
        {customTemplates.length === 0 ? (
          <div className="wb-empty-state compact">
            <Sparkles size={24} />
            <strong>还没有自定义模板</strong>
            <span>运行成功后的结晶动作会出现在这里。</span>
          </div>
        ) : (
          <div className="wb-list">
            {customTemplates.map(template => (
              <div key={template.id} className="wb-list-row">
                <span>{template.name}</span>
                <button className="wb-mini-btn" onClick={() => { useStore.getState().loadScript(template.script); setViewMode('canvas'); }}>打开</button>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

export function SettingsCenter() {
  return (
    <div className="wb-settings-center">
      <div className="wb-settings-intro wb-liquid-glass">
        <StatusChip label="Settings Center" tone="info" />
        <h1>连接、模型、执行、安全和系统控制</h1>
      </div>
      <SettingsPanel />
    </div>
  );
}

export function EvidencePanel({ compact = false }: { compact?: boolean }) {
  const evidenceTiles = [
    { label: 'before', value: '等待截图', tone: 'neutral' as const },
    { label: 'after', value: '等待截图', tone: 'neutral' as const },
    { label: 'diff', value: '接口预留', tone: 'info' as const },
  ];
  const checks = [
    { label: 'done gate', value: '待验证', tone: 'warning' as const },
    { label: 'tool result', value: '等待流入', tone: 'neutral' as const },
    { label: 'trace summary', value: '未绑定', tone: 'info' as const },
  ];

  return (
    <div className={`wb-panel wb-evidence wb-liquid-glass ${compact ? 'compact' : ''}`}>
      <div className="wb-panel-heading">
        <ClipboardCheck size={16} />
        <h2>证据面板</h2>
      </div>
      <div className="wb-evidence-summary">
        <strong>等待第一条 trace</strong>
        <span>运行后自动收集截图、diff、tool result 和 done check。</span>
      </div>
      <details className="wb-evidence-disclosure">
        <summary>
          <span>证据细节</span>
          <ChevronDown size={14} />
        </summary>
        <div className="wb-evidence-tiles">
          {evidenceTiles.map(tile => (
            <div key={tile.label} className="wb-evidence-tile">
              <div className="wb-evidence-preview">
                <span />
                <span />
                <span />
              </div>
              <div>
                <strong>{tile.label}</strong>
                <StatusChip label={tile.value} tone={tile.tone} />
              </div>
            </div>
          ))}
        </div>
        <div className="wb-evidence-grid">
          {checks.map(row => (
            <div key={row.label} className="wb-evidence-row">
              <span>{row.label}</span>
              <StatusChip label={row.value} tone={row.tone} />
            </div>
          ))}
        </div>
        {!compact && (
          <div className="wb-evidence-api">
            <Zap size={14} />
            <span>/api/trace/{'{run_id}'}/summary</span>
          </div>
        )}
      </details>
    </div>
  );
}
