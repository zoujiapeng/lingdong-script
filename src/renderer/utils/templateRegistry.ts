import { FlowScript } from '../types';

export interface TemplateEntry {
  id: string;
  name: string;
  description: string;
  category: string;
  tags: string[];
  aliases: string[];
  keywords: string[];
  source: 'preset' | 'custom' | 'dsl' | 'flow';
  hasScript: boolean;
  flowScript?: FlowScript;
  dslContent?: string;
  thumbnail?: string;
}

export interface ScoredResult {
  template: TemplateEntry;
  score: number;
  reasons: string[];
}

class TemplateRegistry {
  private entries = new Map<string, TemplateEntry>();

  register(entry: TemplateEntry): void {
    this.entries.set(entry.id, entry);
  }

  unregister(id: string): void {
    this.entries.delete(id);
  }

  getAll(): TemplateEntry[] {
    return Array.from(this.entries.values());
  }

  getById(id: string): TemplateEntry | undefined {
    return this.entries.get(id);
  }

  clear(): void {
    this.entries.clear();
  }

  /** Exact match: name → alias → keyword (all normalized) */
  findByExactName(query: string): TemplateEntry | undefined {
    const q = query.trim().toLowerCase();
    // Direct name match first
    for (const t of this.entries.values()) {
      if (t.name.toLowerCase() === q) return t;
    }
    // Alias match
    for (const t of this.entries.values()) {
      if (t.aliases.some(a => a.toLowerCase() === q)) return t;
    }
    // Keyword match
    for (const t of this.entries.values()) {
      if (t.keywords.some(k => k.toLowerCase() === q)) return t;
    }
    return undefined;
  }

  /** Fuzzy match: try partial name/alias match */
  findFuzzy(query: string): TemplateEntry | undefined {
    const q = query.trim().toLowerCase();
    // Remove common Chinese verb prefixes
    const stripped = q.replace(/^(打开|运行|启动|执行|使用|调用)\s*/i, '');
    for (const t of this.entries.values()) {
      const name = t.name.toLowerCase();
      if (name.includes(stripped) || stripped.includes(name)) return t;
      for (const a of t.aliases) {
        const alias = a.toLowerCase();
        if (alias.includes(stripped) || stripped.includes(alias)) return t;
      }
    }
    // Try original query
    if (stripped !== q) return this.findFuzzy(stripped);
    return undefined;
  }

  /** Scored search for semantic matching */
  search(query: string): ScoredResult[] {
    const q = query.trim().toLowerCase();
    const results: ScoredResult[] = [];

    for (const t of this.entries.values()) {
      let score = 0;
      const reasons: string[] = [];

      // Name exact match: 100
      if (t.name.toLowerCase() === q) { score = 100; reasons.push('名称精确匹配'); }
      // Name partial: 70
      else if (t.name.toLowerCase().includes(q) || q.includes(t.name.toLowerCase())) {
        score = Math.max(score, 70); reasons.push('名称部分匹配');
      }

      // Alias exact: 95
      for (const a of t.aliases) {
        if (a.toLowerCase() === q && score < 95) { score = 95; reasons.push('别名匹配'); break; }
      }

      // Keyword match: up to 80
      for (const k of t.keywords) {
        if (k.toLowerCase() === q) { score = Math.max(score, 80); reasons.push('关键字精确匹配'); break; }
        if (q.includes(k.toLowerCase()) || k.toLowerCase().includes(q)) {
          if (score < 60) { score = Math.max(score, 60); reasons.push('关键字: ' + k); }
        }
      }

      // Description word overlap: up to 60
      const descWords = t.description.toLowerCase().split(/[\s,，。、]+/);
      const matchCount = descWords.filter(w => w.length > 1 && q.includes(w)).length;
      if (matchCount > 0) {
        const descScore = Math.min(matchCount * 15, 60);
        if (descScore > score) { score = descScore; reasons.push('描述匹配'); }
      }

      // Tag match: 20 per tag
      for (const tag of t.tags) {
        if (q.includes(tag.toLowerCase())) {
          score += 20; reasons.push('标签: ' + tag);
        }
      }

      // Category match: 10
      if (t.category && q.includes(t.category.toLowerCase())) {
        score += 10; reasons.push('分类: ' + t.category);
      }

      results.push({ template: t, score: Math.min(score, 100), reasons });
    }

    return results
      .filter(r => r.score > 0)
      .sort((a, b) => b.score - a.score);
  }

  /** Generate markdown summary for AI prompts */
  toAIContext(): string {
    const entries = this.getAll();
    if (entries.length === 0) return '';

    let ctx = '\n\n## 可用模板\n如果用户需求与以下某个模板匹配，优先引用模板，不要重新生成：\n\n';

    for (const t of entries) {
      ctx += `### ${t.name} (${t.id})\n`;
      if (t.aliases.length > 0) {
        ctx += `  别名: ${t.aliases.slice(0, 5).join(', ')}\n`;
      }
      ctx += `  用途: ${t.description}\n`;

      // Include internal steps from flowScript if available
      if (t.flowScript?.nodes && t.flowScript.nodes.length > 0) {
        const steps = t.flowScript.nodes
          .filter(n => n.type !== 'start' && n.type !== 'end')
          .slice(0, 6) // Limit to first 6 steps
          .map(n => n.type === 'sub_script'
            ? `调用子脚本: ${(n.params as any)?.script_name || (n.params as any)?.script_id || '?'}`
            : n.type
          );
        if (steps.length > 0) {
          ctx += `  步骤: ${steps.join(' → ')}${t.flowScript.nodes.filter(n => n.type !== 'start' && n.type !== 'end').length > 6 ? '...' : ''}\n`;
        }
      }

      // Tags
      if (t.tags.length > 0) {
        ctx += `  标签: ${t.tags.join(', ')}\n`;
      }

      ctx += '\n';
    }

    ctx += '引用模板的格式：在伪代码中使用 `调用 "模板名"`\n';
    return ctx;
  }

  /** Count */
  get count() { return this.entries.size; }
}

export const templateRegistry = new TemplateRegistry();
