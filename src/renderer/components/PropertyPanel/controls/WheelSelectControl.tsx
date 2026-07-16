import { useState } from 'react';
import { List } from 'lucide-react';

interface Option {
  label: string;
  value: string;
}

interface Props {
  /** 选项列表 */
  options: Option[];
  /** 当前选中值 */
  value: string;
  /** 变更回调 */
  onChange: (value: string) => void;
  /** 标签 */
  label?: string;
  /** 提示文字 */
  title?: string;
}

/** 滚轮选择控件：调用 electronAPI.wheelSelect() 让用户通过全屏滚轮选择 */
export function WheelSelectControl({ options, value, onChange, label = '选择', title }: Props) {
  const [selecting, setSelecting] = useState(false);

  const handleWheelSelect = async () => {
    try {
      setSelecting(true);
      const api = (window as any).electronAPI;
      if (api?.wheelSelect) {
        const result = await api.wheelSelect({
          options: options.map((o) => o.label),
          values: options.map((o) => o.value),
          title: title || label,
          message: `请选择${label}`,
        });
        if (result && result.selected) {
          onChange(result.selected);
        }
      }
    } catch (err: any) {
      console.error('滚轮选择失败:', err);
    } finally {
      setSelecting(false);
    }
  };

  return (
    <div className="form-group">
      <label className="form-label">{label}</label>
      <div style={{ display: 'flex', gap: 4 }}>
        <select
          className="form-input form-select"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          style={{ flex: 1 }}
        >
          {options.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
        <button
          className="btn btn-ghost btn-icon"
          onClick={handleWheelSelect}
          disabled={selecting}
          title="全屏滚轮选择"
          style={{ width: 32, height: 32, flexShrink: 0 }}
        >
          <List size={14} />
        </button>
      </div>
    </div>
  );
}
