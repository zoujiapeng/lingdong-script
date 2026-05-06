import { templateRegistry, TemplateEntry } from './templateRegistry';

export interface ExactMatchResult {
  matched: boolean;
  template: TemplateEntry;
  confidence: 'exact' | 'alias' | 'fuzzy';
  originalQuery: string;
}

/** Strip common Chinese verb prefixes to get at the core name */
function stripChineseVerbs(query: string): string {
  // Common verbs that prefix a template name in Chinese
  return query.replace(/^(打开|运行|启动|执行|使用|调用|创建|新建|自动化|帮我)\s*/i, '');
}

/**
 * Try to match a natural language query to a template.
 * Returns null if no match found.
 */
export function matchExact(query: string): ExactMatchResult | null {
  const q = query.trim();
  if (!q) return null;

  // 1. Try exact name match (case-insensitive)
  const exact = templateRegistry.findByExactName(q);
  if (exact) return { matched: true, template: exact, confidence: 'exact', originalQuery: q };

  // 2. Try with Chinese verb prefix stripped
  const stripped = stripChineseVerbs(q);
  if (stripped !== q) {
    const strippedResult = templateRegistry.findByExactName(stripped);
    if (strippedResult) return { matched: true, template: strippedResult, confidence: 'exact', originalQuery: q };
  }

  // 3. Try fuzzy match (partial name/alias match)
  const fuzzy = templateRegistry.findFuzzy(q);
  if (fuzzy) return { matched: true, template: fuzzy, confidence: 'fuzzy', originalQuery: q };

  // 4. Try stripped + fuzzy
  if (stripped !== q) {
    const fuzzy2 = templateRegistry.findFuzzy(stripped);
    if (fuzzy2) return { matched: true, template: fuzzy2, confidence: 'fuzzy', originalQuery: q };
  }

  return null;
}
