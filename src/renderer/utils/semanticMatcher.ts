import { templateRegistry, ScoredResult } from './templateRegistry';

export interface SemanticMatchResult {
  query: string;
  candidates: ScoredResult[];
  /** true if confidence is high enough to auto-select */
  autoSelect: boolean;
  /** The single best candidate if autoSelect is true */
  best?: ScoredResult;
}

/**
 * Score templates against a natural language query.
 * Returns sorted candidates with the scoring breakdown.
 */
export function matchSemantic(query: string): SemanticMatchResult {
  const results = templateRegistry.search(query);
  const autoSelect = results.length > 0 && results[0].score >= 90;
  return {
    query,
    candidates: results.slice(0, 5),
    autoSelect,
    best: autoSelect ? results[0] : undefined,
  };
}
