// ── HTN (Hierarchical Task Network) 核心类型 ──
// 用于层次化模板规划：复合模板 → 子模板 → 原语指令

/** 节点种类 */
export type HTNNodeKind = 'primitive' | 'composite' | 'abstract';

/** 参数类型 */
export type ParamType = 'string' | 'number' | 'coordinate' | 'image_path' | 'text' | 'region' | 'boolean';

/** 参数采集方式（当无法从用户描述中提取时） */
export type ParamAcquire = 'ask_text' | 'region_select' | 'screenshot' | 'file_picker' | 'wheel_select' | 'ai_infer';

/** 参数定义 */
export interface HTNParameter {
  name: string;
  type: ParamType;
  description: string;
  required: boolean;
  default?: any;
  acquire?: ParamAcquire;
  example?: string;
}

/** 条件类型 */
export type ConditionType = 'if_satisfied' | 'if_failed' | 'always';

/** 回退策略 */
export type FallbackStrategy =
  | { kind: 'skip' }
  | { kind: 'ask_user'; prompt?: string }
  | { kind: 'ai_generate'; hint?: string }
  | { kind: 'substitute'; childId: string };

/** 子步骤条件 */
export interface HTNCondition {
  type: ConditionType;
  check?: string; // 变量/条件表达式引用
}

/** 复合模板中的一个子步骤 */
export interface HTNStep {
  childId: string;
  description?: string;
  optional?: boolean;
  // 父→子参数绑定
  paramBindings?: Record<string, string>;
  condition?: HTNCondition;
  fallback?: FallbackStrategy[];
}

/** 原语指令映射 */
export interface HTNPrimitive {
  dslKeyword: string;          // CLICK, WAIT, TYPE ...
  flowType: string;            // click, wait, input_text ...
  paramBinding: Record<string, string>;  // HTN参数名 → DSL参数名
}

/** HTN 节点（原语 / 复合的统⼀一表示） */
export interface HTNNode {
  id: string;
  name: string;
  description: string;
  aliases: string[];
  keywords: string[];
  tags: string[];
  category: string;
  kind: HTNNodeKind;
  parameters: HTNParameter[];

  // 原语专属
  primitive?: HTNPrimitive;

  // 复合专属
  children?: HTNStep[];

  // 缓存：子树中所有原语节点（展开后）
  flattenedPrimitives?: string[];
}

/** 规划结果中的⼀一个步骤 */
export interface PlanStep {
  nodeId: string;
  nodeName: string;
  kind: HTNNodeKind;
  depth: number;
  bindings: Record<string, any>;   // 最终参数绑定
  dslLine?: string;                // 如果是原语，对应的 DSL 指令
  children?: PlanStep[];           // 如果是复合，展开的内部步骤
  error?: string;
}

/** 完整规划结果 */
export interface PlanResult {
  rootId: string;
  rootName: string;
  score: number;
  steps: PlanStep[];
  dsl: string;          // 扁平化的 DSL 文本
  pseudocode: string;   // 中文伪代码版本
  explanation: string;
}

/** 参数绑定上下文（沿树传播） */
export interface BindingContext {
  /** 用户原始描述中提取的参数 */
  extracted: Record<string, any>;
  /** 已绑定值（含默认值和继承值） */
  resolved: Record<string, any>;
  /** 父节点传入的绑定 */
  inherited: Record<string, any>;
}
