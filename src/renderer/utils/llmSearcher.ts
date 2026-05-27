import { UnifiedSearchResult } from './unifiedSearch';
import { itemMarker } from './itemMarker';

export interface LLMSearchResult {
  enhanced: boolean;
  results: UnifiedSearchResult[];
  llmResponse?: string;
  error?: string;
}

interface LLMMatch {
  id: string;
  reason: string;
  confidence: number;
}

/**
 * If the top offline score is below threshold, call the AI API to re-rank
 * candidates by semantic similarity.
 */
export async function llmEnhanceSearch(
  query: string,
  offlineResults: UnifiedSearchResult[],
  apiKey?: string,
): Promise<LLMSearchResult> {
  const topScore = offlineResults.length > 0 ? offlineResults[0].score : 0;
  if (topScore >= 60 || !apiKey) {
    return { enhanced: false, results: offlineResults };
  }

  const candidates = offlineResults.slice(0, 10).map(r => ({
    id: r.item.id,
    name: r.item.name,
    description: r.item.description,
    type: r.item.type,
    category: r.item.category,
    tags: r.item.tags,
  }));

  const prompt = {
    role: 'user',
    content: `你是一个自动化指令库搜索排序助手。分析每个候选与用户查询的语义相关性。

用户查询: "${query}"

候选列表:
${JSON.stringify(candidates, null, 2)}

返回 JSON 格式:
{ "matches": [ { "id": "...", "reason": "简洁的匹配原因", "confidence": 0-100 } ] }
置信度: 90+完全匹配, 70-89高度相关, 50-69部分相关, <50低相关

只返回 JSON，不要附带其他文字。`,
  };

  try {
    const api = (window as any).electronAPI;
    const response = await api?.callFeature?.('llm_chat', {
      messages: [prompt],
      response_format: 'json',
      temperature: 0.1,
      max_tokens: 2048,
    });

    const content = response?.choices?.[0]?.message?.content;
    if (!content) {
      return { enhanced: false, results: offlineResults, error: 'LLM returned no content' };
    }

    const parsed = JSON.parse(
      typeof content === 'string' ? content.replace(/```(?:json)?\n?/gi, '').trim() : '{}',
    );

    if (parsed.matches && Array.isArray(parsed.matches)) {
      const scoreMap = new Map<string, number>();
      const reasonMap = new Map<string, string>();
      for (const m of parsed.matches as LLMMatch[]) {
        scoreMap.set(m.id, m.confidence);
        if (m.reason) reasonMap.set(m.id, m.reason);
      }

      const merged = offlineResults
        .map(r => {
          const llmConfidence = scoreMap.get(r.item.id);
          if (llmConfidence !== undefined) {
            const blended = Math.round(r.score * 0.6 + llmConfidence * 0.4);
            const newReasons = [...r.reasons];
            const llmReason = reasonMap.get(r.item.id);
            if (llmReason) newReasons.push('AI: ' + llmReason);
            return { ...r, score: blended, reasons: newReasons };
          }
          return r;
        })
        .sort((a, b) => b.score - a.score);

      return { enhanced: true, results: merged, llmResponse: content };
    }

    return { enhanced: false, results: offlineResults };
  } catch (e: any) {
    return { enhanced: false, results: offlineResults, error: e?.message || String(e) };
  }
}

/**
 * Check if LLM API key is configured (from localStorage)
 */
export function hasLLMApiKey(): boolean {
  try {
    return !!(
      (window as any).__llmApiKey ||
      localStorage.getItem('lingdong-llm-api-key')
    );
  } catch {
    return false;
  }
}
