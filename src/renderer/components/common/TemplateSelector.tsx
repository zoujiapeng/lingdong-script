import React from 'react';
import { Play, X, Sparkles, AlertTriangle, Loader2 } from 'lucide-react';
import { ScoredResult } from '../../utils/templateRegistry';

interface TemplateSelectorProps {
  query: string;
  candidates: ScoredResult[];
  loading: boolean;
  onSelect: (result: ScoredResult) => void;
  onGenerate: () => void;
  onCancel: () => void;
}

export function TemplateSelector({
  query,
  candidates,
  loading,
  onSelect,
  onGenerate,
  onCancel,
}: TemplateSelectorProps) {
  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal-content template-selector" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="template-selector-header">
          <Sparkles size={18} />
          <span>匹配到已有模板</span>
          <button className="btn btn-ghost modal-close" onClick={onCancel}>
            <X size={14} />
          </button>
        </div>

        <div className="template-selector-query">
          用户需求: <strong>{query}</strong>
        </div>

        {/* Loading */}
        {loading && (
          <div className="template-selector-loading">
            <Loader2 size={16} className="spin" />
            <span>正在搜索匹配模板...</span>
          </div>
        )}

        {/* Candidates */}
        {!loading && candidates.length === 0 && (
          <div className="template-selector-empty">
            <AlertTriangle size={16} />
            <span>未找到匹配的模板</span>
          </div>
        )}

        {!loading && candidates.length > 0 && (
          <div className="template-selector-list">
            {candidates.map((r, i) => (
              <div key={r.template.id} className="template-selector-item">
                <div className="template-selector-item-rank">#{i + 1}</div>
                <div className="template-selector-item-info">
                  <div className="template-selector-item-name">{r.template.name}</div>
                  <div className="template-selector-item-desc">{r.template.description}</div>
                  <div className="template-selector-item-tags">
                    {r.template.tags.map(tag => (
                      <span key={tag} className="tag">{tag}</span>
                    ))}
                  </div>
                  <div className="template-selector-item-reasons">
                    {r.reasons.map((reason, ri) => (
                      <span key={ri} className="match-reason">{reason}</span>
                    ))}
                  </div>
                </div>
                <div className="template-selector-item-score">
                  <div className="score-bar-container">
                    <div
                      className={`score-bar ${r.score >= 80 ? 'score-high' : r.score >= 50 ? 'score-mid' : 'score-low'}`}
                      style={{ width: `${r.score}%` }}
                    />
                  </div>
                  <span className="score-text">{r.score}%</span>
                </div>
                <button
                  className="btn btn-sm btn-primary"
                  onClick={() => onSelect(r)}
                >
                  <Play size={12} fill="white" />
                  执行
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Footer */}
        {!loading && (
          <div className="template-selector-footer">
            <button className="btn btn-ghost" onClick={onGenerate}>
              <Sparkles size={14} />
              都不符合，生成新的
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
