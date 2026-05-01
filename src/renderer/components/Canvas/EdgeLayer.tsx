import React, { useCallback } from 'react';
import { useStore } from '../../store';
import { FlowNode } from '../../types';
import { NODE_DEFINITIONS } from '../../utils/nodeDefinitions';

function getPortScreenPos(node: FlowNode, portId: string, tx: { x: number; y: number; scale: number }): { x: number; y: number } | null {
  const isStartEnd = node.type === 'start' || node.type === 'end';
  const NW = isStartEnd ? 160 : 220;
  const NH = isStartEnd ? 56 : 80;

  let px: number, py: number;

  if (portId === 'input') {
    px = node.x + NW / 2;
    py = node.y;
  } else {
    const def = NODE_DEFINITIONS[node.type];
    const outputs = def?.ports?.outputs || [];
    const total = outputs.length;
    const idx = outputs.findIndex((p: any) => p.id === portId);
    const leftPct = total <= 1 ? 50 : total === 2 ? (idx === 0 ? 28 : 72) : 20 + idx * 30;
    px = node.x + NW * (leftPct / 100);
    py = node.y + NH;
  }

  return {
    x: px * tx.scale + tx.x,
    y: py * tx.scale + tx.y,
  };
}

function bezierPath(fx: number, fy: number, tx: number, ty: number): string {
  const dy = Math.abs(ty - fy);
  const cp = Math.max(dy * 0.5, 60);
  return `M ${fx} ${fy} C ${fx} ${fy + cp} ${tx} ${ty - cp} ${tx} ${ty}`;
}

const EDGE_COLORS: Record<string, string> = {
  yes: '#22c55e',
  no: '#ef4444',
  fail: '#f59e0b',
  loop_body: '#6366f1',
  loop_end: '#94a3b8',
  output: '#94a3b8',
};

export function EdgeLayer() {
  const { script, ui, selectEdges, deleteEdges } = useStore();
  const { selection } = ui;
  const { canvas_transform } = ui;

  const handleEdgeClick = useCallback(
    (e: React.MouseEvent, edgeId: string) => {
      e.stopPropagation();
      selectEdges([edgeId], e.shiftKey);
    },
    [selectEdges]
  );

  const handleEdgeDoubleClick = useCallback(
    (e: React.MouseEvent, edgeId: string) => {
      e.stopPropagation();
      deleteEdges([edgeId]);
    },
    [deleteEdges]
  );

  const nodeMap = Object.fromEntries(script.nodes.map((n) => [n.id, n]));

  return (
    <svg
      className="edges-svg"
      style={{
        position: 'absolute',
        top: 0,
        right: 0,
        bottom: 0,
        left: 0,
        overflow: 'visible',
      }}
    >
      <defs>
        {['#94a3b8', '#22c55e', '#ef4444', '#f59e0b', '#6366f1', '#3b82f6', '#6366f1'].map((color) => (
          <marker
            key={color}
            id={`arrow-${color.replace('#', '')}`}
            markerWidth="10"
            markerHeight="7"
            refX="8"
            refY="3.5"
            orient="auto"
          >
            <polygon points="0 0, 10 3.5, 0 7" fill={color} />
          </marker>
        ))}
        <marker id="arrow-accent" markerWidth="10" markerHeight="7" refX="8" refY="3.5" orient="auto">
          <polygon points="0 0, 10 3.5, 0 7" fill="var(--accent)" />
        </marker>
      </defs>

      {script.edges.map((edge) => {
        const fromNode = nodeMap[edge.from_node];
        const toNode = nodeMap[edge.to_node];
        if (!fromNode || !toNode) return null;

        const from = getPortScreenPos(fromNode, edge.from_port, canvas_transform);
        const to = getPortScreenPos(toNode, edge.to_port, canvas_transform);
        if (!from || !to) return null;
        const path = bezierPath(from.x, from.y, to.x, to.y);

        const isSelected = selection.edges.includes(edge.id);
        const isAnimated = fromNode.status === 'running' || toNode.status === 'running';

        const baseColor = EDGE_COLORS[edge.from_port] || '#94a3b8';
        const strokeColor = isSelected ? 'var(--accent)' : isAnimated ? '#3b82f6' : baseColor;
        const markerId = isSelected ? 'accent' : isAnimated ? '3b82f6' : baseColor.replace('#', '');

        // Mid-point for label (screen coords)
        const midX = (from.x + to.x) / 2;
        const midY = (from.y + to.y) / 2;

        return (
          <g key={edge.id}>
            {/* Invisible hit area */}
            <path
              d={path}
              fill="none"
              stroke="transparent"
              strokeWidth="16"
              style={{ cursor: 'pointer', pointerEvents: 'all' }}
              onClick={(e) => handleEdgeClick(e, edge.id)}
              onDoubleClick={(e) => handleEdgeDoubleClick(e, edge.id)}
            />
            {/* Visible edge */}
            <path
              d={path}
              fill="none"
              stroke={strokeColor}
              strokeWidth={isSelected ? 2.5 : 2}
              strokeDasharray={isAnimated ? '8 4' : undefined}
              markerEnd={`url(#arrow-${markerId})`}
              style={{
                pointerEvents: 'none',
                transition: 'stroke 0.15s ease',
                animation: isAnimated ? 'flow-dash 0.6s linear infinite' : undefined,
              }}
            />
            {/* Port label */}
            {edge.label && (
              <g>
                <rect
                  x={midX - 18}
                  y={midY - 9}
                  width={36}
                  height={18}
                  rx={9}
                  fill="white"
                  stroke={strokeColor}
                  strokeWidth="1"
                  filter="drop-shadow(0 1px 2px rgba(0,0,0,0.1))"
                />
                <text
                  x={midX}
                  y={midY + 4}
                  textAnchor="middle"
                  fontSize="10"
                  fontWeight="600"
                  fill={strokeColor}
                  fontFamily="Outfit, sans-serif"
                >
                  {edge.label}
                </text>
              </g>
            )}
          </g>
        );
      })}

      <style>{`
        @keyframes flow-dash {
          to { stroke-dashoffset: -12; }
        }
      `}</style>
    </svg>
  );
}
