import { HTNNode, HTNStep, HTNParameter, PlanStep, BindingContext } from './htnTypes';
import { templateRegistry, TemplateEntry } from './templateRegistry';
import { FlowScript } from '../types';

// ── HTN Registry ───────────────────────────────────────────────

class HTNRegistry {
  private nodes = new Map<string, HTNNode>();
  // parentId → childId[]
  private parentToChildren = new Map<string, string[]>();
  // childId → parentId[]
  private childToParents = new Map<string, string[]>();

  // ── 注册 ──

  register(node: HTNNode): void {
    this.nodes.set(node.id, node);
    // Register children relationships
    if (node.children) {
      for (const step of node.children) {
        if (step.childId) {
          this._addEdge(node.id, step.childId);
        }
      }
    }
  }

  /** 从 FlowScript 自动注册（扫描 sub_script 节点） */
  registerFromFlowScript(script: FlowScript): void {
    if (!script || !script.meta) return;
    const existing = this.nodes.get(script.meta.id);
    if (existing) return; // 已注册

    // 收集 sub_script 引用
    const subCalls: HTNStep[] = [];
    for (const node of script.nodes) {
      if (node.type === 'sub_script') {
        const p = node.params as any;
        subCalls.push({
          childId: p.script_id || p.script_name,
          description: p.description || p.script_name,
          optional: false,
        });
      }
    }

    const node: HTNNode = {
      id: script.meta.id,
      name: script.meta.name,
      description: script.meta.description || '',
      aliases: script.meta.aliases || [],
      keywords: script.meta.keywords || [],
      tags: script.meta.tags || [],
      category: script.meta.category || 'custom',
      kind: subCalls.length > 0 ? 'composite' : 'primitive',
      parameters: [],
      children: subCalls.length > 0 ? subCalls : undefined,
    };

    this.register(node);
  }

  // ── 节点类型 → 原语 ID ──
  private static readonly TYPE_TO_PRIM: Record<string, string> = {
    click: 'prim-click',
    click_text: 'prim-click-text',
    image_click: 'prim-click-text',
    input_text: 'prim-input-text',
    wait: 'prim-wait',
    screenshot: 'prim-screenshot',
    keyboard: 'prim-keyboard',
    swipe: 'prim-swipe',
    popup_close: 'prim-popup-close',
    scene_condition: 'prim-scene-check',
    condition: 'prim-scene-check',
    loop: 'prim-loop',
  };

  private static readonly NODE_LABELS: Record<string, string> = {
    click: '点击',
    click_text: '文字点击',
    image_click: '图片点击',
    input_text: '输入文本',
    wait: '等待',
    screenshot: '截图',
    keyboard: '按键',
    swipe: '滑动',
    popup_close: '关闭弹窗',
    scene_condition: '场景判断',
    condition: '条件分支',
    loop: '循环',
    sub_script: '调用子脚本',
    variable_set: '设置变量',
    variable_get: '读取变量',
  };

  /** 从 AI 生成的 FlowScript 节点序列自动注册为复合模板 */
  registerFromFlowNodes(
    query: string,
    nodes: Array<{ type: string; params?: any }>,
  ): string | null {
    if (!query || !nodes || nodes.length < 2) return null;

    // 过滤掉 start/end 节点
    const steps = nodes.filter(n => n.type !== 'start' && n.type !== 'end');
    if (steps.length < 2) return null;

    // 生成节点类型签名（去重检测用）
    const typeSig = steps.map(n => n.type).join('→');

    // 检查是否已有相同模式的模板
    for (const existing of this.getAll()) {
      if (existing.children) {
        const childSig = existing.children.map(c => {
          const cn = this.nodes.get(c.childId);
          const nodeDef = HTNRegistry.TYPE_TO_PRIM;
          return Object.entries(nodeDef).find(([, v]) => v === c.childId)?.[0] || c.childId;
        }).join('→');
        if (childSig === typeSig) return null; // 相同模式已存在
      }
    }

    // 检查是否已有足够相似的模板（注册表搜索命中率 >= 50 就不重复注册）
    const existing = this.search(query);
    if (existing.length > 0 && existing[0].score >= 50) return null;

    // 提取关键词（从用户查询中取有意义的词）
    const keywords = this._extractKeywords(query);

    // 生成 ID 和名称
    const id = `auto-${Date.now().toString(36)}`;
    const name = query.length > 25 ? query.slice(0, 25) + '…' : query;

    // 构建子步骤
    const children: HTNStep[] = steps.map((n, i) => ({
      childId: HTNRegistry.TYPE_TO_PRIM[n.type] || 'prim-wait',
      description: HTNRegistry.NODE_LABELS[n.type] || n.type,
      optional: n.type === 'popup_close' || n.type === 'screenshot',
      paramBindings: {},
    }));

    const node: HTNNode = {
      id,
      name,
      description: query,
      aliases: [],
      keywords,
      tags: ['auto-registered'],
      category: 'custom',
      kind: 'composite',
      parameters: [],
      children,
    };

    this.register(node);
    return id;
  }

  /** 从查询文本中提取关键词 */
  private _extractKeywords(text: string): string[] {
    // 去除标点和语气词
    const cleaned = text.replace(/[，。、！？；：""''（）【】《》\s,.!?;:()\[\]{}]/g, ' ');
    const words = cleaned.split(/\s+/).filter(w => w.length >= 2);
    // 去重
    return [...new Set(words)];
  }

  /** 从 YAML 定义注册复合模板 */
  registerFromYaml(yaml: any): HTNNode | null {
    if (!yaml || !yaml.name) return null;
    const id = yaml.id || `htn-${yaml.name.replace(/\s+/g, '-').toLowerCase()}`;
    const children: HTNStep[] = (yaml.children || []).map((c: any) => ({
      childId: c.childId || c.templateRef || '',
      description: c.description || '',
      optional: !!c.optional,
      paramBindings: c.paramBindings || c.param_bindings || {},
      condition: c.condition,
      fallback: c.fallback,
    }));

    const parameters: HTNParameter[] = (yaml.parameters || []).map((p: any) => ({
      name: p.name,
      type: p.type || 'string',
      description: p.description || '',
      required: p.required !== false,
      default: p.default,
      acquire: p.acquire,
      example: p.example,
    }));

    const node: HTNNode = {
      id,
      name: yaml.name,
      description: yaml.description || '',
      aliases: yaml.aliases || [],
      keywords: yaml.keywords || [],
      tags: yaml.tags || [],
      category: yaml.category || 'general',
      kind: children.length > 0 ? 'composite' : 'primitive',
      parameters,
      children: children.length > 0 ? children : undefined,
      primitive: yaml.primitive,
    };

    this.register(node);
    return node;
  }

  private _addEdge(parentId: string, childId: string): void {
    if (!this.parentToChildren.has(parentId)) {
      this.parentToChildren.set(parentId, []);
    }
    const kids = this.parentToChildren.get(parentId)!;
    if (!kids.includes(childId)) kids.push(childId);

    if (!this.childToParents.has(childId)) {
      this.childToParents.set(childId, []);
    }
    const parents = this.childToParents.get(childId)!;
    if (!parents.includes(parentId)) parents.push(parentId);
  }

  // ── 查询 ──

  get(id: string): HTNNode | undefined {
    return this.nodes.get(id);
  }

  getAll(): HTNNode[] {
    return Array.from(this.nodes.values());
  }

  /** 根节点（没有父节点的复合节点，原语不作为根节点） */
  getRoots(): HTNNode[] {
    return this.getAll().filter(n => {
      if (n.kind === 'primitive') return false;
      const parents = this.childToParents.get(n.id);
      return !parents || parents.length === 0;
    });
  }

  /** 叶子节点（原语，没有子节点） */
  getPrimitives(): HTNNode[] {
    return this.getAll().filter(n => n.kind === 'primitive' || !n.children || n.children.length === 0);
  }

  getChildren(id: string): HTNNode[] {
    return (this.parentToChildren.get(id) || [])
      .map(cid => this.nodes.get(cid))
      .filter(Boolean) as HTNNode[];
  }

  getParents(id: string): HTNNode[] {
    return (this.childToParents.get(id) || [])
      .map(pid => this.nodes.get(pid))
      .filter(Boolean) as HTNNode[];
  }

  /** 展开子树中所有原语节点 */
  flatten(id: string): string[] {
    const node = this.nodes.get(id);
    if (!node) return [];
    if (node.flattenedPrimitives) return node.flattenedPrimitives;

    if (!node.children || node.children.length === 0) {
      node.flattenedPrimitives = [id];
      return node.flattenedPrimitives;
    }

    const result: string[] = [];
    for (const step of node.children) {
      result.push(...this.flatten(step.childId));
    }
    node.flattenedPrimitives = result;
    return result;
  }

  /** 结构深度（根为 1，每层 +1） */
  depth(id: string): number {
    const children = this.parentToChildren.get(id);
    if (!children || children.length === 0) return 1;
    return 1 + Math.max(...children.map(cid => this.depth(cid)));
  }

  /** 模板的"规模"（展开后的原语个数），用于 structure_bonus */
  structureBonus(id: string): number {
    return this.flatten(id).length;
  }

  // ── 搜索（语义+结构加权） ──

  search(query: string): Array<{ node: HTNNode; score: number; reasons: string[] }> {
    const q = query.trim().toLowerCase();
    const results: Array<{ node: HTNNode; score: number; reasons: string[] }> = [];

    for (const node of this.nodes.values()) {
      let score = 0;
      const reasons: string[] = [];

      // 名称/别名/关键字匹配（同 templateRegistry.search）
      if (node.name.toLowerCase() === q) { score = 100; reasons.push('名称精确匹配'); }
      else if (node.name.toLowerCase().includes(q)) { score = Math.max(score, 70); reasons.push('名称部分匹配'); }

      for (const a of node.aliases) {
        if (a.toLowerCase() === q && score < 95) { score = 95; reasons.push('别名匹配'); break; }
      }
      let kwMatchCount = 0;
      let kwPrefixBonus = 0;
      for (const k of node.keywords) {
        if (k.toLowerCase() === q) { score = Math.max(score, 80); kwMatchCount = 99; reasons.push('关键字精确匹配'); break; }
        if (q.includes(k.toLowerCase())) {
          kwMatchCount++;
          if (kwMatchCount === 1) { score = Math.max(score, 35); reasons.push('关键字: ' + k); }
          // 查询以关键词开头 → 更相关的信号（剩余部分必须短于关键词本身）
          if (q.startsWith(k.toLowerCase()) && k.length >= 2) {
            const remainder = q.slice(k.length).trim();
            if (remainder.length <= k.length) {
              kwPrefixBonus = 15;
            }
          }
        }
      }
      if (kwPrefixBonus) score += kwPrefixBonus;
      if (kwMatchCount > 1 && kwMatchCount < 99) {
        const extraKw = Math.min((kwMatchCount - 1) * 12, 36); // 每个额外关键字 +12，最多 +36
        score += extraKw;
        reasons.push(`匹配 ${kwMatchCount} 个关键字`);
      }

      // ⭐ structure_bonus：大模板优先匹配
      const bonus = this.structureBonus(node.id);
      if (bonus > 1) {
        let structureScore = Math.min(bonus * 5, 20); // 最多 +20（减少结构偏差）
        // 如果只有一个短关键词匹配且关键词不到查询的一半，取消结构分（防误匹配）
        if (kwMatchCount === 1 && q.length >= 4) {
          const firstKw = node.keywords.find(k => q.includes(k.toLowerCase()));
          if (firstKw && firstKw.length <= q.length / 2) {
            structureScore = 0;
          }
        }
        score += structureScore;
        if (structureScore > 0) reasons.push(`结构规模: ${bonus} 步`);
      }

      // 深度加成
      const d = this.depth(node.id);
      if (d > 1) {
        score += Math.min((d - 1) * 5, 10); // 每层 +5，最多 +10（减少深度偏差）
        if ((d - 1) * 5 > 0) reasons.push(`层次深度: ${d} 层`);
      }

      // 基础原语降权（prim- 前缀的基元节点是原子操作，不应作为顶层匹配）
      if (node.kind === 'primitive' && node.id.startsWith('prim-')) {
        score = Math.max(score - 25, 0);
      }

      results.push({ node, score: Math.min(score, 130), reasons });
    }

    return results
      .filter(r => {
        // 最少需要有关键词/名称/别名匹配，纯结构分不纳入结果
        if (r.score > 30) return true;
        return r.reasons.some(rez => !rez.startsWith('结构规模') && !rez.startsWith('层次深度'));
      })
      .sort((a, b) => b.score - a.score);
  }

  // ── AI 上下文生成（层次化展示） ──

  toAIContext(): string {
    const roots = this.getRoots();
    if (roots.length === 0) return '';

    let ctx = '\n\n## 层次化模板（HTN）\n按优先级从高到低匹配用户需求：\n\n';

    for (const root of roots) {
      ctx += this._nodeToContext(root, 0);
    }

    ctx += '\n规划规则：优先匹配顶层复合模板，然后逐层分解。'
      + '如果顶层不匹配，尝试下一级。都不匹配则用 AI 从原语生成。\n';

    return ctx;
  }

  private _nodeToContext(node: HTNNode, indent: number): string {
    const prefix = '  '.repeat(indent);
    let line = `${prefix}- ${node.name}`;
    if (node.aliases.length) line += ` [${node.aliases.slice(0, 3).join(', ')}]`;
    line += `: ${node.description}\n`;

    if (node.parameters.length > 0) {
      const required = node.parameters.filter(p => p.required).map(p => p.name).join(', ');
      if (required) line += `${prefix}  参数: ${required}\n`;
    }

    if (node.children && indent < 3) {
      for (const step of node.children) {
        const child = this.nodes.get(step.childId);
        if (child) {
          line += this._nodeToContext(child, indent + 1);
        }
      }
    }

    return line;
  }

  // ── 已有模板自动注册 ──

  /** 从 templateRegistry 中所有 FlowScript 自动推断层次 */
  autoRegisterFromTemplates(): void {
    for (const entry of templateRegistry.getAll()) {
      if (entry.flowScript) {
        this.registerFromFlowScript(entry.flowScript);
      }
    }
  }

  /** 计数器 */
  get count() { return this.nodes.size; }
}

export const htnRegistry = new HTNRegistry();
