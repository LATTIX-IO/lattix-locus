"use client";

import { useMemo } from "react";

export type RunGraphNode = {
  id: string;
  title: string;
  type: string;
  x: number;
  y: number;
  config?: Record<string, unknown>;
};

export type RunGraphLink = {
  from: string;
  to: string;
  from_port?: string;
  to_port?: string;
};

type Props = {
  nodes: RunGraphNode[];
  links: RunGraphLink[];
  height?: number;
};

const NODE_W = 168;
const NODE_H = 56;
const PAD = 40;

const TYPE_COLOR: Record<string, string> = {
  trigger: "hsl(48 90% 55%)",
  agent: "hsl(210 80% 60%)",
  output: "hsl(150 60% 50%)",
  tool: "hsl(280 60% 65%)",
  guardrail: "hsl(0 70% 60%)",
  router: "hsl(190 70% 55%)",
};

function colorForType(type: string): string {
  return TYPE_COLOR[type] ?? "hsl(220 10% 60%)";
}

/**
 * Dependency-free read-only execution-graph renderer.
 *
 * Replaces the heavy ReactFlow canvas for the run-detail snapshot: ReactFlow
 * mounted inside the collapsible Details flyout would initialize against a
 * zero-size / hidden container and render blank. This lays nodes out from their
 * stored x/y (auto-columns when coordinates are absent) and draws links as SVG
 * paths — it always renders and cannot fail on mount.
 */
export function RunGraphView({ nodes, links, height = 460 }: Props) {
  const layout = useMemo(() => {
    if (nodes.length === 0) {
      return { positioned: [], width: 600, contentHeight: height };
    }
    // Auto-column layout when coordinates collapse onto one point.
    const distinctX = new Set(nodes.map((n) => Math.round(n.x))).size;
    const usable = distinctX > 1;
    const positioned = nodes.map((node, index) => {
      const col = usable ? node.x : index;
      const row = usable ? node.y : 0;
      return { node, col, row };
    });

    const minX = Math.min(...positioned.map((p) => p.col));
    const maxX = Math.max(...positioned.map((p) => p.col));
    const minY = Math.min(...positioned.map((p) => p.row));
    const maxY = Math.max(...positioned.map((p) => p.row));
    const spanX = maxX - minX || 1;
    const spanY = maxY - minY || 1;

    const colGap = 220;
    const rowGap = 96;
    const cols = usable ? Math.max(1, Math.round(spanX / 200) + 1) : nodes.length;
    const width = PAD * 2 + Math.max(1, cols - 1) * colGap + NODE_W;

    const place = positioned.map(({ node, col, row }, index) => {
      const px = usable
        ? PAD + ((col - minX) / spanX) * (Math.max(1, cols - 1) * colGap)
        : PAD + index * colGap;
      const py = usable ? PAD + ((row - minY) / spanY) * (rowGap * 2) : PAD + 60;
      return { node, x: px, y: py };
    });

    const contentHeight = Math.max(
      height,
      PAD * 2 + (place.reduce((m, p) => Math.max(m, p.y), 0) - PAD) + NODE_H,
    );
    return { positioned: place, width, contentHeight };
  }, [nodes, height]);

  const centerById = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>();
    for (const p of layout.positioned) {
      map.set(p.node.id, { x: p.x + NODE_W / 2, y: p.y + NODE_H / 2 });
    }
    return map;
  }, [layout.positioned]);

  if (nodes.length === 0) {
    return (
      <div className="fx-panel flex items-center justify-center p-6 text-sm fx-muted" style={{ height }}>
        No execution graph for this run.
      </div>
    );
  }

  return (
    <div className="fx-panel overflow-auto" style={{ height }}>
      <svg
        width={layout.width}
        height={layout.contentHeight}
        viewBox={`0 0 ${layout.width} ${layout.contentHeight}`}
        className="min-w-full"
      >
        <defs>
          <marker
            id="run-graph-arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--fx-muted, #7b8794)" />
          </marker>
        </defs>

        {links.map((link, index) => {
          const from = centerById.get(link.from);
          const to = centerById.get(link.to);
          if (!from || !to) return null;
          const startX = from.x + NODE_W / 2 - 2;
          const endX = to.x - NODE_W / 2 + 2;
          const midX = (startX + endX) / 2;
          return (
            <path
              key={`${link.from}-${link.to}-${index}`}
              d={`M ${startX} ${from.y} C ${midX} ${from.y}, ${midX} ${to.y}, ${endX} ${to.y}`}
              fill="none"
              stroke="var(--fx-muted, #7b8794)"
              strokeWidth={1.6}
              markerEnd="url(#run-graph-arrow)"
            />
          );
        })}

        {layout.positioned.map(({ node, x, y }) => {
          const accent = colorForType(node.type);
          return (
            <g key={node.id} transform={`translate(${x}, ${y})`}>
              <rect
                width={NODE_W}
                height={NODE_H}
                rx={10}
                fill="var(--fx-surface, #1a1f26)"
                stroke="var(--fx-border, #303843)"
              />
              <rect width={4} height={NODE_H} rx={2} fill={accent} />
              <text x={16} y={23} fontSize={12} fontWeight={600} fill="var(--foreground, #e6e8eb)">
                {node.title.length > 20 ? `${node.title.slice(0, 19)}…` : node.title}
              </text>
              <text x={16} y={41} fontSize={10} fill="var(--fx-muted, #8b96a3)">
                {node.type}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
