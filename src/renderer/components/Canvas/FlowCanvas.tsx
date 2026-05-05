import React, { useRef, useCallback, useEffect, useState } from 'react';
import { useStore } from '../../store';
import { FlowNode as FlowNodeComp } from './FlowNode';
import { EdgeLayer } from './EdgeLayer';
import { CanvasControls, MiniMap, ContextMenu } from './CanvasControls';
import { NODE_DEFINITIONS } from '../../utils/nodeDefinitions';
import { NodeType, FlowNode } from '../../types';

export function FlowCanvas() {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const {
    script, ui,
    setCanvasTransform, addNode,
    clearSelection, selectNodes, deleteNodes, deleteEdges,
    startConnecting, stopConnecting, addEdge,
    setHoveredNode,
  } = useStore();

  const [isPanning, setIsPanning] = useState(false);
  const [panStart, setPanStart] = useState({ x: 0, y: 0 });
  const panRAF = useRef<number>(0);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });
  const [selectionRect, setSelectionRect] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  const [selectionStart, setSelectionStart] = useState<{ x: number; y: number } | null>(null);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; canvasX: number; canvasY: number } | null>(null);
  const [connectingEnd, setConnectingEnd] = useState<{ x: number; y: number } | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const { canvas_transform, connecting, selection } = ui;

  // Convert screen coords to canvas coords
  const screenToCanvas = useCallback(
    (sx: number, sy: number) => {
      const rect = wrapperRef.current!.getBoundingClientRect();
      return {
        x: (sx - rect.left - canvas_transform.x) / canvas_transform.scale,
        y: (sy - rect.top - canvas_transform.y) / canvas_transform.scale,
      };
    },
    [canvas_transform]
  );

  const getNodePortScreenPos = useCallback(
    (nodeId: string, portId: string) => {
      const node = script.nodes.find((n) => n.id === nodeId);
      if (!node) return null;
      const def = NODE_DEFINITIONS[node.type];
      const NODE_W = node.type === 'start' || node.type === 'end' ? 160 : 220;
      const NODE_H = node.type === 'start' || node.type === 'end' ? 56 : 80;

      let px: number, py: number;

      if (portId === 'input') {
        px = node.x + NODE_W / 2;
        py = node.y;
      } else {
        // Dynamically calculate output port position matching FlowNode.tsx
        const outputs = def?.ports?.outputs || [];
        const total = outputs.length;
        const idx = outputs.findIndex((p: any) => p.id === portId);
        const leftPct = total <= 1 ? 50 : total === 2 ? (idx === 0 ? 28 : 72) : 20 + idx * 30;
        px = node.x + NODE_W * (leftPct / 100);
        py = node.y + NODE_H;
      }

      return {
        x: px * canvas_transform.scale + canvas_transform.x,
        y: py * canvas_transform.scale + canvas_transform.y,
      };
    },
    [script.nodes, canvas_transform]
  );

  // Wheel to zoom
  const handleWheel = useCallback(
    (e: WheelEvent) => {
      e.preventDefault();
      const rect = wrapperRef.current!.getBoundingClientRect();
      const cx = e.clientX - rect.left;
      const cy = e.clientY - rect.top;
      const delta = e.deltaY < 0 ? 1.1 : 0.9;
      const newScale = Math.min(Math.max(canvas_transform.scale * delta, 0.15), 4);
      const newX = cx - (cx - canvas_transform.x) * (newScale / canvas_transform.scale);
      const newY = cy - (cy - canvas_transform.y) * (newScale / canvas_transform.scale);
      setCanvasTransform({ x: newX, y: newY, scale: newScale });
    },
    [canvas_transform, setCanvasTransform]
  );

  useEffect(() => {
    const el = wrapperRef.current;
    if (!el) return;
    el.addEventListener('wheel', handleWheel, { passive: false });
    return () => {
      el.removeEventListener('wheel', handleWheel);
      cancelAnimationFrame(panRAF.current);
    };
  }, [handleWheel]);

  // Delete/Backspace key to delete selected nodes and edges
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Skip if user is typing in an input field
      const target = e.target as HTMLElement;
      if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target.isContentEditable) return;

      if (e.key === 'Delete' || e.key === 'Backspace') {
        const st = useStore.getState();
        if (st.ui.selection.nodes.length > 0) {
          st.deleteNodes(st.ui.selection.nodes);
        }
        if (st.ui.selection.edges.length > 0) {
          st.deleteEdges(st.ui.selection.edges);
        }
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, []);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (e.button === 1 || (e.button === 0 && e.altKey)) {
        // Middle button or alt+click = pan
        setIsPanning(true);
        setPanStart({ x: e.clientX - canvas_transform.x, y: e.clientY - canvas_transform.y });
        return;
      }
      if (e.button === 0) {
        const target = e.target as HTMLElement;
        const isCanvasBg = target === wrapperRef.current
          || target.classList.contains('canvas-bg')
          || target.classList.contains('canvas-grid');
        if (isCanvasBg) {
          // Read fresh connecting state from store to avoid stale closure
          if (useStore.getState().ui.connecting.active) {
            stopConnecting();
            return;
          }
          clearSelection();
          // Start selection rect
          const canvasPos = screenToCanvas(e.clientX, e.clientY);
          setSelectionStart(canvasPos);
          setSelectionRect({ x: canvasPos.x, y: canvasPos.y, w: 0, h: 0 });
        }
        // Click on edge SVG empty area = canvas click (paths have stopPropagation on click)
        if (target.closest('.edges-svg') && !(target as Element).closest('g')) {
          if (useStore.getState().ui.connecting.active) {
            stopConnecting();
            return;
          }
          clearSelection();
        }
      }
      if (e.button === 2) {
        e.preventDefault();
      }
    },
    [canvas_transform, clearSelection, stopConnecting, screenToCanvas]
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      setMousePos({ x: e.clientX, y: e.clientY });

      if (isPanning) {
        cancelAnimationFrame(panRAF.current);
        const px = e.clientX, py = e.clientY;
        panRAF.current = requestAnimationFrame(() => {
          useStore.setState((state) => {
            state.ui.canvas_transform.x = px - panStart.x;
            state.ui.canvas_transform.y = py - panStart.y;
          });
        });
        return;
      }

      if (useStore.getState().ui.connecting.active) {
        const rect = wrapperRef.current!.getBoundingClientRect();
        setConnectingEnd({ x: e.clientX - rect.left, y: e.clientY - rect.top });
        return;
      }

      if (selectionStart) {
        const canvasPos = screenToCanvas(e.clientX, e.clientY);
        setSelectionRect({
          x: Math.min(selectionStart.x, canvasPos.x),
          y: Math.min(selectionStart.y, canvasPos.y),
          w: Math.abs(canvasPos.x - selectionStart.x),
          h: Math.abs(canvasPos.y - selectionStart.y),
        });
      }
    },
    [isPanning, panStart, setCanvasTransform, selectionStart, screenToCanvas]
  );

  const handleMouseUp = useCallback(
    (e: React.MouseEvent) => {
      if (isPanning) { setIsPanning(false); return; }

      // If connecting, check if mouse is released over a target node
      const cn = useStore.getState().ui.connecting;
      console.log('[CANVAS] handleMouseUp connecting:', cn.active, 'from:', cn.from_node);
      if (cn.active && cn.from_node) {
        const canvasPos = screenToCanvas(e.clientX, e.clientY);
        const targetNode = script.nodes.find((n) => {
          if (n.id === cn.from_node) return false;
          const NW = n.type === 'start' || n.type === 'end' ? 160 : 220;
          const NH = n.type === 'start' || n.type === 'end' ? 56 : 80;
          return (
            canvasPos.x >= n.x &&
            canvasPos.x <= n.x + NW &&
            canvasPos.y >= n.y &&
            canvasPos.y <= n.y + NH
          );
        });
        if (targetNode) {
          addEdge({
            from_node: cn.from_node,
            from_port: cn.from_port || 'output',
            to_node: targetNode.id,
            to_port: 'input',
          });
        }
        stopConnecting();
        setConnectingEnd(null);
        return;
      }

      if (selectionStart && selectionRect && (selectionRect.w > 5 || selectionRect.h > 5)) {
        // Select nodes within rect
        const selected = script.nodes
          .filter((n) => {
            const NW = n.type === 'start' || n.type === 'end' ? 160 : 220;
            const NH = n.type === 'start' || n.type === 'end' ? 56 : 80;
            return (
              n.x + NW > selectionRect.x &&
              n.x < selectionRect.x + selectionRect.w &&
              n.y + NH > selectionRect.y &&
              n.y < selectionRect.y + selectionRect.h
            );
          })
          .map((n) => n.id);
        if (selected.length > 0) selectNodes(selected);
      }

      setSelectionStart(null);
      setSelectionRect(null);
    },
    [isPanning, selectionStart, selectionRect, script.nodes, selectNodes, addEdge, stopConnecting, screenToCanvas]
  );

  const handleContextMenu = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      const canvasPos = screenToCanvas(e.clientX, e.clientY);
      setContextMenu({ x: e.clientX, y: e.clientY, canvasX: canvasPos.x, canvasY: canvasPos.y });
    },
    [screenToCanvas]
  );

  // Drag and drop from node library
  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    setDragOver(true);
  };

  const handleDragLeave = () => setDragOver(false);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const nodeType = e.dataTransfer.getData('node-type') as NodeType;
      if (!nodeType) return;
      const canvasPos = screenToCanvas(e.clientX, e.clientY);
      const NW = 220, NH = 80;
      const id = addNode(nodeType, canvasPos.x - NW / 2, canvasPos.y - NH / 2);

      // If dropping a feature tool → pre-fill feature_name
      const featureName = e.dataTransfer.getData('feature-name');
      if (featureName && nodeType === 'tool_call') {
        useStore.getState().updateNodeParams(id, { feature_name: featureName, params: {} });
      }
    },
    [screenToCanvas, addNode]
  );

  // Port connection handler
  const handlePortMouseDown = useCallback(
    (e: React.MouseEvent, nodeId: string, portId: string) => {
      e.stopPropagation();
      console.log('[CANVAS] handlePortMouseDown', nodeId, portId);
      if (portId === 'input') return; // can't drag from input
      startConnecting(nodeId, portId);
      const rect = wrapperRef.current!.getBoundingClientRect();
      setConnectingEnd({ x: e.clientX - rect.left, y: e.clientY - rect.top });
      console.log('[CANVAS] connecting started, connectingEnd set');
    },
    [startConnecting]
  );

  const handlePortMouseUp = useCallback(
    (e: React.MouseEvent, nodeId: string, portId: string) => {
      e.stopPropagation();
      const st = useStore.getState().ui.connecting;
      console.log('[CANVAS] handlePortMouseUp', nodeId, portId, 'connecting:', st.active, 'from:', st.from_node);
      if (!st.active) return;
      if (st.from_node && portId === 'input') {
        if (st.from_node !== nodeId) {
          console.log('[CANVAS] adding edge', st.from_node, '->', nodeId);
          addEdge({
            from_node: st.from_node,
            from_port: st.from_port || 'output',
            to_node: nodeId,
            to_port: 'input',
          });
        }
      }
      // Always stop connecting — even if released on output port or non-target
      stopConnecting();
      setConnectingEnd(null);
      console.log('[CANVAS] connection ended, stopConnecting called');
    },
    [addEdge, stopConnecting]
  );

  // Build grid SVG pattern
  const { show_grid, grid_size } = ui;
  const gridSVG = show_grid ? (
    <svg className="canvas-grid" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}>
      <defs>
        <pattern id="smallGrid" width={grid_size} height={grid_size} patternUnits="userSpaceOnUse">
          <path d={`M ${grid_size} 0 L 0 0 0 ${grid_size}`} fill="none" stroke="rgba(148,163,184,0.18)" strokeWidth="0.7" />
        </pattern>
        <pattern id="bigGrid" width={grid_size * 5} height={grid_size * 5} patternUnits="userSpaceOnUse">
          <rect width={grid_size * 5} height={grid_size * 5} fill="url(#smallGrid)" />
          <path d={`M ${grid_size * 5} 0 L 0 0 0 ${grid_size * 5}`} fill="none" stroke="rgba(148,163,184,0.3)" strokeWidth="1" />
        </pattern>
      </defs>
      <rect width="100%" height="100%" fill="url(#bigGrid)" />
    </svg>
  ) : null;

  // Connecting line
  const connectingLine = connecting.active && connecting.from_node && connectingEnd ? (() => {
    console.log('[CANVAS] rendering connecting line, from:', connecting.from_node, 'port:', connecting.from_port);
    const fromPos = getNodePortScreenPos(connecting.from_node!, connecting.from_port || 'output');
    if (!fromPos) return null;
    const rect = wrapperRef.current?.getBoundingClientRect();
    if (!rect) return null;
    const fx = fromPos.x - rect.left;
    const fy = fromPos.y - rect.top;
    const tx = connectingEnd.x;
    const ty = connectingEnd.y;
    const cy1 = fy + Math.abs(ty - fy) * 0.5;
    const cy2 = ty - Math.abs(ty - fy) * 0.5;
    return (
      <svg className="connecting-edge" style={{ position: 'absolute', inset: 0, pointerEvents: 'none', overflow: 'visible' }}>
        <path
          d={`M ${fx} ${fy} C ${fx} ${cy1} ${tx} ${cy2} ${tx} ${ty}`}
          fill="none"
          stroke="var(--accent)"
          strokeWidth="2"
          strokeDasharray="6 3"
        />
        <circle cx={tx} cy={ty} r="4" fill="var(--accent)" />
      </svg>
    );
  })() : null;

  return (
    <div
      ref={wrapperRef}
      className={`flow-canvas-wrapper ${isPanning ? 'is-panning' : ''} ${connecting.active ? 'is-connecting' : ''} ${dragOver ? 'drop-zone-active' : ''}`}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onContextMenu={handleContextMenu}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      onClick={() => contextMenu && setContextMenu(null)}
    >
      <div className="canvas-bg" />

      {/* Main transform group */}
      <div
        className="canvas-transform"
        style={{ transform: `translate(${canvas_transform.x}px, ${canvas_transform.y}px) scale(${canvas_transform.scale})` }}
      >
        {gridSVG}

        {/* Nodes */}
        {script.nodes.map((node) => (
          <FlowNodeComp
            key={node.id}
            node={node}
            onPortMouseDown={handlePortMouseDown}
            onPortMouseUp={handlePortMouseUp}
          />
        ))}

        {/* Selection rect */}
        {selectionRect && (selectionRect.w > 5 || selectionRect.h > 5) && (
          <div
            className="selection-rect"
            style={{
              left: selectionRect.x,
              top: selectionRect.y,
              width: selectionRect.w,
              height: selectionRect.h,
            }}
          />
        )}
      </div>

      {/* Edges (screen space, same CSS transform as nodes) */}
      <EdgeLayer />

      {/* Connecting line (screen space) */}
      {connectingLine}

      {/* Canvas controls */}
      <CanvasControls />

      {/* Mini map */}
      <MiniMap />

      {/* Context menu */}
      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          canvasX={contextMenu.canvasX}
          canvasY={contextMenu.canvasY}
          onClose={() => setContextMenu(null)}
        />
      )}
    </div>
  );
}
