import React from 'react';
import { ChevronRight, ArrowLeft } from 'lucide-react';
import { useStore } from '../../store';

export function SubFlowBreadcrumb() {
  const stack = useStore((s) => s.subFlowNav.stack);
  const script = useStore((s) => s.script);
  const navigateBackFromSubFlow = useStore((s) => s.navigateBackFromSubFlow);

  if (stack.length === 0) return null;

  const handleBack = () => navigateBackFromSubFlow();

  return (
    <div className="sub-flow-breadcrumb">
      <button className="btn btn-ghost btn-sm" onClick={handleBack} title="返回上一级">
        <ArrowLeft size={14} />
      </button>
      {stack.map((entry, i) => {
        const isLast = i === stack.length - 1;
        return (
          <React.Fragment key={entry.scriptId + i}>
            <span
              className={`breadcrumb-item ${isLast ? '' : 'breadcrumb-clickable'}`}
              onClick={() => {
                if (!isLast) {
                  // Jump directly to this level
                  const target = useStore.getState().scripts[entry.scriptId];
                  if (target) {
                    useStore.getState().navigateToSubFlow(entry.scriptId);
                  }
                }
              }}
            >
              {entry.scriptName}
            </span>
            {!isLast && <ChevronRight size={12} className="breadcrumb-sep" />}
          </React.Fragment>
        );
      })}
      <ChevronRight size={12} className="breadcrumb-sep" />
      <span className="breadcrumb-item breadcrumb-current">{script.meta.name}</span>
    </div>
  );
}
