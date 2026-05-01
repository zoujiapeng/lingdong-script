import React from 'react';
import { AIAssistant } from './AIAssistant';
import { X } from 'lucide-react';

export function AIAssistantDialog({ onClose }: { onClose: () => void }) {
  return (
    <div className="ai-dialog-content">
      <div className="ai-dialog-header">
        <span>AI 助手</span>
        <button className="ai-dialog-close-btn" onClick={onClose} title="关闭">
          <X size={16} />
        </button>
      </div>
      <AIAssistant />
    </div>
  );
}
