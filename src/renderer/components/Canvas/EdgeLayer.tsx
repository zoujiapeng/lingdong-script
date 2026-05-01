import React, { useCallback } from 'react';
import { useStore } from '../../store';
import { FlowNode, FlowEdge } from '../../types';
import { NODE_DEFINITIONS } from '../../utils/nodeDefinitions';

function getPortOffset(node: FlowNode, portId: string): { x: number; y: number } {
  const isStartEnd = node.type === 'start' || node.type === 'end';
  const NW = isStartEnd ? 160 : 220;
  const NH = isStartEnd ? 56 : 80;

  if (portId === 'input') return { x: node.x + NW / 2, y: node.y };
  if (portId === 'output') return { x: node.x + NW / 2, y: node.y + NH };
  if (portId === 'yes') return { x: node.x + NW * 0.28, y: node.y + NH };
  if (portId === 'no') return { x: node.x + NW * 0.72, y: node.y + NH };
  if (portId === 'fail') return { x: node.x + NW * 0.72, y: node.y + NH };
  if (portId === 'loop_body') return { x: node.x + NW * 0.28, y: node.y + NH };
  if (portId === 'loop_end') return { x: node.x + NW * 0.72, y: node.y + NH };
  return { x: node.x + NW / 2, y: node.y + NH };
}

function bezierPath(fx: number, fy: number, tx: number, ty: number): string {
  const dy = Math.abs(ty - fy);
  const cp = Math.max(dy * 0.5, 60);
  const cy1y = fy + cp;
  const cy2y = ty - cp;
  return `M ${fx} ${fy} C ${fx} ${cy1y} ${tx} ${cy2y} ${tx} ${ty}`;
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
        left: 0,
        width: '100%',
        height: '100%',
        overflow: 'visible',
        pointerEvents: 'none',
      }}
    >
      <defs>
        {/* Arrowheads for different colors */}
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

        const from = getPortOffset(fromNode, edge.from_port);
        const to = getPortOffset(toNode, edge.to_port);
        const path = bezierPath(from.x, from.y, to.x, to.y);

        const isSelected = selection.edges.includes(edge.id);
        const isAnimated = fromNode.status === 'running' || toNode.status === 'running';

        const baseColor = EDGE_COLORS[edge.from_port] || '#94a3b8';
        const strokeColor = isSelected ? 'var(--accent)' : isAnimated ? '#3b82f6' : baseColor;
        const markerId = isSelected ? 'accent' : isAnimated ? '3b82f6' : baseColor.replace('#', '');

        // Mid-point for label
        const midX = (from.x + to.x) / 2;
        const midY = (from.y + to.y) / 2;

        return (
          <g key={edge.id} style={{ pointerEvents: 'auto' }}>
            {/* Invisible hit area */}
            <path
              d={path}
              fill="none"
              stroke="transparent"
              strokeWidth="16"
              style={{ cursor: 'pointer' }}
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
