import { htnRegistry } from './htnRegistry';
import {
  HTNNode, HTNStep, PlanStep, PlanResult, BindingContext,
} from './htnTypes';

// ── HTN Planner (Hierarchical Task Network Decomposer) ─────────

export class HTNPlanner {
  /**
   * Main entry: natural language query → decomposed PlanResult.
   * Returns null when no template matches at all (caller should fall
   * through to AI generation).
   */
  plan(query: string, extractedParams?: Record<string, any>): PlanResult | null {
    const candidates = htnRegistry.search(query);
    if (candidates.length === 0) return null;

    const top = candidates[0];
    const context: BindingContext = {
      extracted: extractedParams || {},
      resolved: { ...(extractedParams || {}) },
      inherited: {},
    };

    this._resolveDefaults(top.node, context);

    const rootStep = this._decompose(top.node.id, context, 0);
    if (!rootStep) return null;

    return this._buildResult(top.node, rootStep, context, top.score, top.reasons);
  }

  // ── Recursive Decomposition ──

  private _decompose(
    nodeId: string,
    context: BindingContext,
    depth: number,
    ancestors?: Set<string>,
  ): PlanStep | null {
    const node = htnRegistry.get(nodeId);
    if (!node) return null;

    // Cycle detection: if this node is already an ancestor, we have a cycle
    if (ancestors?.has(nodeId)) {
      return null;
    }
    const path = ancestors || new Set<string>();
    path.add(nodeId);

    this._resolveDefaults(node, context);

    const step: PlanStep = {
      nodeId: node.id,
      nodeName: node.name,
      kind: node.kind,
      depth,
      bindings: { ...context.resolved },
    };

    // ── Primitive leaf ──
    if (node.kind === 'primitive' || !node.children || node.children.length === 0) {
      path.delete(nodeId);
      const dsl = this._generateDSL(node, context);
      if (!dsl) return null;
      step.dslLine = dsl;
      return step;
    }

    // ── Composite: decompose each child ──
    const children: PlanStep[] = [];
    let hasError = false;

    for (const child of node.children) {
      const childNode = htnRegistry.get(child.childId);
      const childContext = this._buildChildContext(child, context);

      if (!childNode) {
        const fb = this._handleFallback(child, childContext, depth + 1, path);
        if (fb) {
          children.push(fb);
        } else if (!child.optional) {
          hasError = true;
          step.error = `缺少必需子步骤: ${child.description || child.childId}`;
          break;
        }
        continue;
      }

      const childStep = this._decompose(child.childId, childContext, depth + 1, path);
      if (childStep) {
        children.push(childStep);
      } else {
        const fb = this._handleFallback(child, childContext, depth + 1, path);
        if (fb) {
          children.push(fb);
        } else if (!child.optional) {
          hasError = true;
          step.error = `子步骤分解失败: ${child.description || child.childId}`;
          break;
        }
      }
    }

    path.delete(nodeId);

    if (hasError || children.length === 0) {
      return null;
    }

    step.children = children;
    step.dslLine = this._flattenDSL(children);
    return step;
  }

  // ── Context / Binding Helpers ──

  private _buildChildContext(
    child: HTNStep,
    parentContext: BindingContext,
  ): BindingContext {
    const childResolved: Record<string, any> = { ...parentContext.resolved };

    for (const [childParam, binding] of Object.entries(child.paramBindings || {})) {
      if (typeof binding === 'string') {
        if (binding.startsWith('$')) {
          const parentKey = binding.slice(1);
          if (parentContext.resolved[parentKey] !== undefined) {
            childResolved[childParam] = parentContext.resolved[parentKey];
          }
        } else {
          childResolved[childParam] = this._coerceParam(binding);
        }
      } else if (typeof binding === 'number' || typeof binding === 'boolean') {
        childResolved[childParam] = binding;
      }
      // Object-type expressions (e.g., { $add: [...] }) are skipped for now
    }

    return {
      extracted: parentContext.extracted,
      resolved: childResolved,
      inherited: { ...parentContext.resolved },
    };
  }

  private _resolveDefaults(node: HTNNode, context: BindingContext): void {
    for (const param of node.parameters) {
      if (context.resolved[param.name] === undefined) {
        if (param.default !== undefined) {
          context.resolved[param.name] = param.default;
        } else if (param.type === 'boolean') {
          context.resolved[param.name] = false;
        }
      }
    }
  }

  /** Parse string literals into typed values */
  private _coerceParam(value: string): any {
    if (value === 'true') return true;
    if (value === 'false') return false;
    if (/^\d+$/.test(value)) return parseInt(value, 10);
    if (/^\d+\.\d+$/.test(value)) return parseFloat(value);
    return value;
  }

  // ── DSL Generation ──

  private _generateDSL(node: HTNNode, context: BindingContext): string | null {
    const prim = node.primitive;
    if (!prim) {
      // Primitive without explicit mapping — use node name as hint
      return `// ${node.name}: ${node.description || '原语操作'}`;
    }

    const params = context.resolved;
    const args: string[] = [];

    for (const [htnParam, dslParam] of Object.entries(prim.paramBinding)) {
      const value = params[htnParam] ?? params[dslParam] ?? '';
      args.push(String(value));
    }

    const dsl = `${prim.dslKeyword} ${args.join(' ')}`.trim();
    return dsl || null;
  }

  private _flattenDSL(steps: PlanStep[], indent = 0): string {
    const lines: string[] = [];
    const pad = '  '.repeat(indent);
    for (const s of steps) {
      if (s.dslLine) {
        lines.push(pad + s.dslLine);
      }
      if (s.children) {
        lines.push(this._flattenDSL(s.children, indent + 1));
      }
    }
    return lines.join('\n');
  }

  // ── Fallback Handling ──

  private _handleFallback(
    child: HTNStep,
    context: BindingContext,
    depth: number,
    ancestors?: Set<string>,
  ): PlanStep | null {
    if (!child.fallback || child.fallback.length === 0) return null;

    for (const fb of child.fallback) {
      switch (fb.kind) {
        case 'skip':
          return {
            nodeId: 'skip',
            nodeName: '跳过',
            kind: 'primitive',
            depth,
            bindings: {},
            dslLine: '// 已跳过',
          };

        case 'ai_generate': {
          return {
            nodeId: 'ai_generate',
            nodeName: 'AI 生成',
            kind: 'primitive',
            depth,
            bindings: {},
            dslLine: `// AI 生成: ${fb.hint || child.description || child.childId}`,
          };
        }

        case 'ask_user': {
          return {
            nodeId: 'ask_user',
            nodeName: '询问用户',
            kind: 'primitive',
            depth,
            bindings: {},
            dslLine: `// 需要用户确认: ${fb.prompt || child.description || child.childId}`,
          };
        }

        case 'substitute': {
          const subNode = htnRegistry.get(fb.childId);
          if (subNode) {
            const subContext: BindingContext = {
              extracted: context.extracted,
              resolved: { ...context.resolved },
              inherited: context.inherited,
            };
            return this._decompose(fb.childId, subContext, depth, ancestors);
          }
          break;
        }
      }
    }

    return null;
  }

  // ── Result Builders ──

  private _buildResult(
    node: HTNNode,
    rootStep: PlanStep,
    context: BindingContext,
    score: number,
    reasons: string[],
  ): PlanResult {
    const flatSteps = this._collectPrimitives(rootStep);
    const dsl = this._flattenDSL([rootStep]);
    // pseudocode is in executable format for parsePseudoToFlow
    const pseudocode = this._generateExecutablePseudocode(rootStep);
    const explanation = this._generateExplanation(node, score, reasons, context);

    return {
      rootId: node.id,
      rootName: node.name,
      score,
      steps: flatSteps,
      dsl,
      pseudocode,
      explanation,
    };
  }

  // ── Tree Traversal ──

  /** DFS to collect all primitive steps into a flat ordered list */
  private _collectPrimitives(step: PlanStep): PlanStep[] {
    if (!step.children || step.children.length === 0) {
      return step.dslLine ? [step] : [];
    }
    const result: PlanStep[] = [];
    for (const c of step.children) {
      result.push(...this._collectPrimitives(c));
    }
    return result;
  }

  // ── Pseudocode Generation ──

  /** Mapping from flowType to pseudocode template function */
  private static readonly PSEUDO_FORMATS: Record<string, (args: Record<string, any>) => string> = {
    click:        (a) => `点击 (${a.x ?? '?'}, ${a.y ?? '?'})`,
    click_text:   (a) => `文字点击 "${a.text ?? ''}"`,
    input_text:   (a) => `输入 "${a.text ?? ''}" 位置 (${a.x ?? '?'}, ${a.y ?? '?'})`,
    wait:         (a) => `等待 ${a.duration ?? 1}`,
    screenshot:   (_) => `截图`,
    keyboard:     (a) => `按键 ${a.keys ?? ''}`,
    swipe:        (a) => `滑动 (${a.start_x ?? '?'},${a.start_y ?? '?'}) 到 (${a.end_x ?? '?'},${a.end_y ?? '?'})`,
    popup_close:  (_) => `关闭弹窗`,
    scene_condition: (a) => `场景匹配 "${a.scene_name ?? ''}"`,
    loop:         (a) => `循环 ${a.count ?? 10}:`,
  };

  /**
   * Generate executable pseudocode in the format parsePseudoToFlow expects
   * (Chinese keywords → FlowScript conversion).
   */
  private _generateExecutablePseudocode(rootStep: PlanStep): string {
    const primitives = this._collectPrimitives(rootStep);
    const lines: string[] = [];

    for (const p of primitives) {
      const node = htnRegistry.get(p.nodeId);
      if (!node || !node.primitive) {
        if (p.dslLine && !p.dslLine.startsWith('//')) {
          lines.push(p.dslLine);
        }
        continue;
      }

      const formatFn = HTNPlanner.PSEUDO_FORMATS[node.primitive.flowType];
      if (formatFn) {
        lines.push(formatFn(p.bindings));
      } else if (p.dslLine && !p.dslLine.startsWith('//')) {
        lines.push(p.dslLine);
      }
    }

    return lines.join('\n');
  }

  /**
   * Public helper: convert any PlanResult to executable pseudocode.
   * Useful when the caller needs pseudocode after planning.
   */
  toExecutablePseudocode(plan: PlanResult): string {
    if (plan.steps.length > 0) {
      // Reconstruct a synthetic root step from the flat steps array
      const root: PlanStep = {
        nodeId: plan.rootId,
        nodeName: plan.rootName,
        kind: 'composite',
        depth: 0,
        bindings: {},
        children: plan.steps,
      };
      return this._generateExecutablePseudocode(root);
    }
    return plan.pseudocode;
  }

  private _generateExplanation(
    node: HTNNode,
    score: number,
    reasons: string[],
    context: BindingContext,
  ): string {
    const parts: string[] = [
      `匹配模板: ${node.name}（${Math.round(score)}分）`,
      `原因: ${reasons.join('、')}`,
    ];

    // Show resolved parameters
    const resolvedKeys = Object.keys(context.resolved).filter(
      k => context.resolved[k] !== undefined && context.resolved[k] !== '',
    );
    if (resolvedKeys.length > 0) {
      parts.push(`参数: ${resolvedKeys.map(k => `${k}=${context.resolved[k]}`).join(', ')}`);
    }

    // Hierarchy info
    const depth = htnRegistry.depth(node.id);
    if (depth > 1) {
      parts.push(`模板深度: ${depth}层`);
    }

    const size = htnRegistry.structureBonus(node.id);
    if (size > 1) {
      parts.push(`展开步骤: ${size}步`);
    }

    return parts.join('\n');
  }
}

/** Singleton instance */
export const htnPlanner = new HTNPlanner();
