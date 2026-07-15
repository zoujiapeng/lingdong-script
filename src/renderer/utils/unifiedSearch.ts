import { FlowScript, FeatureDefinition, NodeType } from '../types';
import { templateRegistry, TemplateEntry } from './templateRegistry';
import { itemMarker } from './itemMarker';
import { NODE_DEFINITIONS } from './nodeDefinitions';
import { isRunnableFlowNodeType } from '../platform/flowToArpGraph';

export type SearchItemType = 'feature' | 'template' | 'node';

export interface UnifiedSearchItem {
  id: string;
  name: string;
  description: string;
  type: SearchItemType;
  aliases: string[];
  keywords: string[];
  tags: string[];
  category: string;
  // Feature-specific
  featureName?: string;
  params?: import('../types').FeatureParam[];
  dslKeyword?: string;
  // Template-specific
  source?: 'preset' | 'custom' | 'dsl' | 'flow';
  hasScript?: boolean;
  flowScript?: FlowScript;
  // Node-specific
  nodeType?: NodeType;
  color?: string;
  icon?: string;
  // Metadata
  tested: boolean;
}

export interface UnifiedSearchResult {
  item: UnifiedSearchItem;
  score: number;
  reasons: string[];
}

export interface UnifiedSearchOptions {
  types?: SearchItemType[];
  maxResults?: number;
  minScore?: number;
}

// Common Chinese verb prefixes to strip for better matching
const VERB_PREFIXES = /^(打开|运行|启动|执行|使用|调用|创建|新建|自动化|帮我|给我|需要|找一下|搜索|查找|寻找)\s*/i;

function normalizeQuery(q: string): string {
  return q.trim().toLowerCase().replace(VERB_PREFIXES, '');
}

class UnifiedSearchEngine {
  private index: UnifiedSearchItem[] = [];
  private built = false;

  /** Rebuild from executable templates and compiler-supported nodes only. */
  rebuildIndex(_features?: FeatureDefinition[]): void {
    const index: UnifiedSearchItem[] = [];

    // 1. Add template registry entries
    for (const t of templateRegistry.getAll()) {
      index.push(this.templateToItem(t));
    }

    // 2. Add native node definitions.
    for (const def of Object.values(NODE_DEFINITIONS)) {
      if (!isRunnableFlowNodeType(def.type)) continue;
      index.push({
        id: def.type,
        name: def.label,
        description: def.description || '',
        type: 'node',
        aliases: [def.type, def.label],
        keywords: [def.type, def.label, ...(def.category ? [def.category] : [])],
        tags: [def.category || ''],
        category: def.category || '',
        nodeType: def.type as NodeType,
        color: def.color,
        icon: def.icon,
        tested: itemMarker.isTested(def.type, 'node'),
      });
    }

    this.index = index;
    this.built = true;
  }

  private templateToItem(t: TemplateEntry): UnifiedSearchItem {
    return {
      id: t.id,
      name: t.name,
      description: t.description,
      type: 'template',
      aliases: t.aliases,
      keywords: t.keywords,
      tags: t.tags,
      category: t.category,
      source: t.source,
      hasScript: t.hasScript,
      flowScript: t.flowScript,
      tested: itemMarker.isTested(t.id, 'template'),
    };
  }

  /** Ensure index is built before searching */
  private ensure(): void {
    if (!this.built) {
      // Try to build from available data
      this.rebuildIndex();
    }
  }

  /** Force re-check tested status on existing items without full rebuild */
  refreshTestedStatus(): void {
    for (const item of this.index) {
      item.tested = itemMarker.isTested(item.id, item.type as 'feature' | 'template' | 'node');
    }
  }

  /** Search across all indexed items */
  search(query: string, options?: UnifiedSearchOptions): UnifiedSearchResult[] {
    this.ensure();
    this.refreshTestedStatus();

    const q = query.trim().toLowerCase();
    if (!q) return [];

    const stripped = normalizeQuery(q);
    const results: UnifiedSearchResult[] = [];

    for (const item of this.index) {
      // Type filter
      if (options?.types && !options.types.includes(item.type)) continue;

      let score = 0;
      const reasons: string[] = [];

      // 1. Name exact match: 100
      if (item.name.toLowerCase() === q || item.name.toLowerCase() === stripped) {
        score = 100;
        reasons.push('名称精确匹配');
      }
      // 2. Name partial: 70
      else if (item.name.toLowerCase().includes(q) || item.name.toLowerCase().includes(stripped) ||
               q.includes(item.name.toLowerCase()) || stripped.includes(item.name.toLowerCase())) {
        score = Math.max(score, 70);
        reasons.push('名称部分匹配');
      }

      // 3. Alias exact: 95
      for (const a of item.aliases) {
        const alias = a.toLowerCase();
        if (alias === q || alias === stripped) {
          if (score < 95) { score = 95; reasons.push('别名匹配'); }
          break;
        }
      }

      // 4. Alias partial: 65
      if (score < 95) {
        for (const a of item.aliases) {
          const alias = a.toLowerCase();
          if (alias.includes(q) || alias.includes(stripped) ||
              q.includes(alias) || stripped.includes(alias)) {
            if (score < 65) { score = Math.max(score, 65); reasons.push('别名部分匹配'); }
            break;
          }
        }
      }

      // 5. Keyword matches: base 50, +10 per extra, max 80
      const kwMatches = item.keywords.filter(k => {
        const kw = k.toLowerCase();
        return q.includes(kw) || stripped.includes(kw) || kw.includes(q) || kw.includes(stripped);
      });
      if (kwMatches.length > 0) {
        const kwScore = Math.min(50 + (kwMatches.length - 1) * 10, 80);
        if (kwScore > score) {
          score = kwScore;
          reasons.push('关键词: ' + kwMatches.join(', '));
        }
      }

      // 6. Tag matches: +15 per tag (bonus, not base)
      const tagScore = item.tags.filter(t => q.includes(t.toLowerCase()) || stripped.includes(t.toLowerCase())).length * 15;
      if (tagScore > 0) {
        score += tagScore;
        reasons.push('标签匹配');
      }

      // 7. Category match: +10
      if (item.category && (q.includes(item.category.toLowerCase()) || stripped.includes(item.category.toLowerCase()))) {
        score += 10;
        reasons.push('分类: ' + item.category);
      }

      // 8. Description overlap: up to 50
      const descWords = item.description.toLowerCase().split(/[\s,，。、：:；;！!？?()（）\[\]【】]+/);
      const matchCount = descWords.filter(w => w.length > 1 && (q.includes(w) || stripped.includes(w))).length;
      if (matchCount > 0) {
        const descScore = Math.min(matchCount * 15, 50);
        score += descScore;
        if (descScore > 0 && !reasons.length) reasons.push('描述匹配');
      }

      // 9. Tested badge bonus: +5
      if (item.tested) {
        score += 5;
        reasons.push('已验证');
      }

      if (score > 0) {
        results.push({ item, score: Math.min(score, 150), reasons });
      }
    }

    let sorted = results.sort((a, b) => b.score - a.score);

    if (options?.minScore !== undefined) {
      sorted = sorted.filter(r => r.score >= options!.minScore!);
    }

    if (options?.maxResults !== undefined) {
      sorted = sorted.slice(0, options.maxResults);
    }

    return sorted;
  }

  /** Get item by id and type */
  getById(id: string, type: SearchItemType): UnifiedSearchItem | undefined {
    this.ensure();
    return this.index.find(item => item.id === id && item.type === type);
  }

  /** Get all indexed items */
  getAll(): UnifiedSearchItem[] {
    this.ensure();
    return [...this.index];
  }

  get count(): number {
    return this.index.length;
  }
}

export const unifiedSearch = new UnifiedSearchEngine();
