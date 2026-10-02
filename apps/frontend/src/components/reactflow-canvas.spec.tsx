import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ReactFlowCanvas, type GraphLink, type GraphNode } from "@/components/reactflow-canvas";

const reactFlowPropsSpy = vi.fn();

vi.mock("reactflow", () => {
  type MockNodeComponentProps = {
    id: string;
    data: unknown;
    type?: unknown;
    selected?: boolean;
    dragging?: boolean;
    zIndex?: number;
    isConnectable?: boolean;
    xPos?: number;
    yPos?: number;
    positionAbsoluteX?: number;
    positionAbsoluteY?: number;
  };

  function applySelectionChanges<T extends { id: string; selected?: boolean }>(
    changes: Array<{ id?: string; type?: string; selected?: boolean }>,
    items: T[],
  ): T[] {
    return items.map((item) => {
      const selectChange = changes.find((change) => change.type === "select" && change.id === item.id);
      if (!selectChange) {
        return item;
      }
      return {
        ...item,
        selected: Boolean(selectChange.selected),
      };
    });
  }

  return {
    Background: () => <div data-testid="rf-background" />,
    Controls: ({ style }: { style?: React.CSSProperties }) => <div data-testid="rf-controls" style={style} />,
    MiniMap: ({ style }: { style?: React.CSSProperties }) => <div data-testid="rf-minimap" style={style} />,
    Position: {
      Left: "left",
      Right: "right",
      Top: "top",
      Bottom: "bottom",
    },
    ReactFlowProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Handle: ({ id, type }: { id: string; type: string }) => <div data-testid={`handle-${type}-${id}`} />,
    addEdge: (edge: Record<string, unknown>, edges: Array<Record<string, unknown>>) => [...edges, edge],
    applyEdgeChanges: (changes: Array<{ id?: string; type?: string; selected?: boolean }>, edges: Array<{ id: string; selected?: boolean }>) => applySelectionChanges(changes, edges),
    applyNodeChanges: (changes: Array<{ id?: string; type?: string; selected?: boolean }>, nodes: Array<{ id: string; selected?: boolean }>) => applySelectionChanges(changes, nodes),
    ReactFlow: (props: Record<string, unknown>) => {
      reactFlowPropsSpy(props);
      const nodeTypes = (props.nodeTypes ?? {}) as Record<string, React.ComponentType<MockNodeComponentProps>>;
      const nodes = Array.isArray(props.nodes) ? (props.nodes as Array<Record<string, unknown>>) : [];
      React.useEffect(() => {
        const onInit = props.onInit as undefined | ((instance: { fitView: () => void; screenToFlowPosition: (point: { x: number; y: number }) => { x: number; y: number } }) => void);
        onInit?.({
          fitView: () => undefined,
          screenToFlowPosition: (point) => point,
        });
      }, [props]);

      return (
        <div>
          {props.children as React.ReactNode}
          {nodes.map((node) => {
            const NodeComponent = nodeTypes[String(node.type)];
            if (!NodeComponent) {
              return null;
            }
            return (
              <NodeComponent
                key={String(node.id)}
                id={String(node.id)}
                data={node.data}
                type={node.type}
                selected={Boolean(node.selected)}
                dragging={false}
                zIndex={0}
                isConnectable
                xPos={0}
                yPos={0}
                positionAbsoluteX={0}
                positionAbsoluteY={0}
              />
            );
          })}
          <button
            type="button"
            onClick={() => {
              const onNodesChange = props.onNodesChange as undefined | ((changes: Array<Record<string, unknown>>) => void);
              onNodesChange?.([{ id: "run-trigger", type: "select", selected: true }]);
            }}
          >
            select run-trigger
          </button>
          <button
            type="button"
            onClick={() => {
              const onEdgesChange = props.onEdgesChange as undefined | ((changes: Array<Record<string, unknown>>) => void);
              onEdgesChange?.([{ id: "run-trigger:out->run-agent:in:0", type: "select", selected: true }]);
            }}
          >
            select first edge
          </button>
          <button
            type="button"
            onClick={() => {
              const onConnect = props.onConnect as undefined | ((connection: Record<string, unknown>) => void);
              onConnect?.({ source: "router", target: "branch-a", sourceHandle: "match_a", targetHandle: "in" });
            }}
          >
            connect match_a
          </button>
          <button
            type="button"
            onClick={() => {
              const onConnect = props.onConnect as undefined | ((connection: Record<string, unknown>) => void);
              onConnect?.({ source: "router", target: "branch-b", sourceHandle: "match_b", targetHandle: "in" });
            }}
          >
            connect match_b
          </button>
          <button
            type="button"
            onClick={() => {
              const onConnect = props.onConnect as undefined | ((connection: Record<string, unknown>) => void);
              onConnect?.({ source: "router", target: "branch-default", sourceHandle: "default", targetHandle: "in" });
            }}
          >
            connect default
          </button>
          <button
            type="button"
            onClick={() => {
              const onConnect = props.onConnect as undefined | ((connection: Record<string, unknown>) => void);
              onConnect?.({ source: "iterator", target: "iter-loop", sourceHandle: "loop", targetHandle: "in" });
            }}
          >
            connect loop
          </button>
          <button
            type="button"
            onClick={() => {
              const onConnect = props.onConnect as undefined | ((connection: Record<string, unknown>) => void);
              onConnect?.({ source: "iterator", target: "iter-done", sourceHandle: "done", targetHandle: "in" });
            }}
          >
            connect done
          </button>
          <button
            type="button"
            onClick={() => {
              const onConnect = props.onConnect as undefined | ((connection: Record<string, unknown>) => void);
              onConnect?.({ source: "wait", target: "wait-resume", sourceHandle: "resume", targetHandle: "in" });
            }}
          >
            connect wait resume
          </button>
          <button
            type="button"
            onClick={() => {
              const onConnect = props.onConnect as undefined | ((connection: Record<string, unknown>) => void);
              onConnect?.({ source: "wait", target: "wait-timeout", sourceHandle: "timeout", targetHandle: "in" });
            }}
          >
            connect wait timeout
          </button>
          <button
            type="button"
            onClick={() => {
              const onConnect = props.onConnect as undefined | ((connection: Record<string, unknown>) => void);
              onConnect?.({ source: "event", target: "event-resume", sourceHandle: "resume", targetHandle: "in" });
            }}
          >
            connect event resume
          </button>
          <button
            type="button"
            onClick={() => {
              const onConnect = props.onConnect as undefined | ((connection: Record<string, unknown>) => void);
              onConnect?.({ source: "event", target: "event-idle", sourceHandle: "idle", targetHandle: "in" });
            }}
          >
            connect event idle
          </button>
          <button
            type="button"
            onClick={() => {
              const isValidConnection = props.isValidConnection as undefined | ((connection: Record<string, unknown>) => boolean);
              const valid = isValidConnection?.({ source: "router", target: "branch-a", sourceHandle: "decision", targetHandle: "in" }) ?? false;
              const marker = document.createElement("div");
              marker.setAttribute("data-testid", "invalid-decision-connection");
              marker.textContent = String(valid);
              document.body.appendChild(marker);
            }}
          >
            validate decision connection
          </button>
        </div>
      );
    },
  };
});

describe("ReactFlowCanvas", () => {
  beforeEach(() => {
    reactFlowPropsSpy.mockClear();
    document.querySelector('[data-testid="invalid-decision-connection"]')?.remove();
  });

  function renderCanvas(onGraphChange = vi.fn()) {
    const nodes: GraphNode[] = [
      { id: "router", title: "Priority Router", type: "locus/router", x: 0, y: 0, config: {} },
      { id: "branch-a", title: "Branch A", type: "locus/transform", x: 300, y: 0, config: {} },
      { id: "branch-b", title: "Branch B", type: "locus/transform", x: 300, y: 100, config: {} },
      { id: "branch-default", title: "Branch Default", type: "locus/transform", x: 300, y: 200, config: {} },
      { id: "iterator", title: "Iterator", type: "locus/iterator", x: 0, y: 320, config: {} },
      { id: "iter-loop", title: "Iterate", type: "locus/transform", x: 300, y: 320, config: {} },
      { id: "iter-done", title: "Done", type: "locus/output", x: 300, y: 420, config: {} },
      { id: "wait", title: "Wait", type: "locus/wait", x: 0, y: 540, config: {} },
      { id: "wait-resume", title: "Resume Branch", type: "locus/transform", x: 300, y: 540, config: {} },
      { id: "wait-timeout", title: "Timeout Branch", type: "locus/error-handler", x: 300, y: 640, config: {} },
      { id: "event", title: "Event", type: "locus/event", x: 0, y: 760, config: {} },
      { id: "event-resume", title: "Event Resume", type: "locus/transform", x: 300, y: 760, config: {} },
      { id: "event-idle", title: "Event Idle", type: "locus/error-handler", x: 300, y: 860, config: {} },
    ];
    const links: GraphLink[] = [];

    render(<ReactFlowCanvas nodes={nodes} links={links} onGraphChange={onGraphChange} />);
    return onGraphChange;
  }

  it("serializes router branch connections for match_a, match_b, and default handles", async () => {
    const onGraphChange = renderCanvas();

    fireEvent.click(screen.getByRole("button", { name: /connect match_a/i }));
    fireEvent.click(screen.getByRole("button", { name: /connect match_b/i }));
    fireEvent.click(screen.getByRole("button", { name: /connect default/i }));

    await waitFor(() => {
      expect(onGraphChange).toHaveBeenCalled();
      const latestGraph = onGraphChange.mock.calls.at(-1)?.[0] as { links: GraphLink[] };
      expect(latestGraph.links).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ from: "router", to: "branch-a", from_port: "match_a", to_port: "in" }),
          expect.objectContaining({ from: "router", to: "branch-b", from_port: "match_b", to_port: "in" }),
          expect.objectContaining({ from: "router", to: "branch-default", from_port: "default", to_port: "in" }),
        ]),
      );
    });
  });

  it("rejects connecting router decision data output into a flow input handle", async () => {
    renderCanvas();

    fireEvent.click(screen.getByRole("button", { name: /validate decision connection/i }));

    await waitFor(() => {
      expect(screen.getByTestId("invalid-decision-connection")).toHaveTextContent("false");
    });
  });

  it("serializes iterator branch connections for loop and done handles", async () => {
    const onGraphChange = renderCanvas();

    fireEvent.click(screen.getByRole("button", { name: /connect loop/i }));
    fireEvent.click(screen.getByRole("button", { name: /connect done/i }));

    await waitFor(() => {
      const latestGraph = onGraphChange.mock.calls.at(-1)?.[0] as { links: GraphLink[] };
      expect(latestGraph.links).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ from: "iterator", to: "iter-loop", from_port: "loop", to_port: "in" }),
          expect.objectContaining({ from: "iterator", to: "iter-done", from_port: "done", to_port: "in" }),
        ]),
      );
    });
  });

  it("serializes wait branch connections for resume and timeout handles", async () => {
    const onGraphChange = renderCanvas();

    fireEvent.click(screen.getByRole("button", { name: /connect wait resume/i }));
    fireEvent.click(screen.getByRole("button", { name: /connect wait timeout/i }));

    await waitFor(() => {
      const latestGraph = onGraphChange.mock.calls.at(-1)?.[0] as { links: GraphLink[] };
      expect(latestGraph.links).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ from: "wait", to: "wait-resume", from_port: "resume", to_port: "in" }),
          expect.objectContaining({ from: "wait", to: "wait-timeout", from_port: "timeout", to_port: "in" }),
        ]),
      );
    });
  });

  it("serializes event branch connections for resume and idle handles", async () => {
    const onGraphChange = renderCanvas();

    fireEvent.click(screen.getByRole("button", { name: /connect event resume/i }));
    fireEvent.click(screen.getByRole("button", { name: /connect event idle/i }));

    await waitFor(() => {
      const latestGraph = onGraphChange.mock.calls.at(-1)?.[0] as { links: GraphLink[] };
      expect(latestGraph.links).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ from: "event", to: "event-resume", from_port: "resume", to_port: "in" }),
          expect.objectContaining({ from: "event", to: "event-idle", from_port: "idle", to_port: "in" }),
        ]),
      );
    });
  });

  it("labels branch-only outputs as control-flow branches in the builder UI", () => {
    renderCanvas();

    expect(screen.getByTitle("match_a (control-flow branch)")).toBeInTheDocument();
    expect(screen.getByTitle("loop (control-flow branch)")).toBeInTheDocument();
    expect(screen.getAllByTitle("resume (control-flow branch)")).toHaveLength(2);
    expect(screen.getByTitle("idle (control-flow branch)")).toBeInTheDocument();
  });

  it("renders the lower-left canvas navigation at a reduced size", () => {
    renderCanvas();

    expect(screen.getByTestId("rf-minimap")).toHaveStyle({ width: "140px", height: "105px" });
    expect(screen.getByTestId("rf-controls")).toHaveStyle({ transform: "scale(0.7)", transformOrigin: "bottom left" });
  });

  it("serializes agent skills as a structured string array", async () => {
    const onGraphChange = vi.fn();
    const nodes: GraphNode[] = [
      { id: "agent-1", title: "Agent", type: "locus/agent", x: 120, y: 90, config: { agent_id: "agent-1", system_prompt: "Respond precisely." } },
    ];

    render(
      <ReactFlowCanvas
        nodes={nodes}
        links={[]}
        onGraphChange={onGraphChange}
        widgetOptionOverrides={{ agent: { skills: ["/personal-research", "/tenant-oncall"] } }}
      />,
    );

    fireEvent.change(screen.getByLabelText("skills"), {
      target: { value: "/incident-triage\n/personal-research" },
    });

    fireEvent.click(screen.getByRole("button", { name: "/tenant-oncall" }));

    await waitFor(() => {
      expect(onGraphChange).toHaveBeenCalled();
      const latestGraph = onGraphChange.mock.calls.at(-1)?.[0] as { nodes: GraphNode[] };
      expect(latestGraph.nodes[0]?.config?.skills).toEqual([
        "/incident-triage",
        "/personal-research",
        "/tenant-oncall",
      ]);
    });
  });

  it("updates rendered nodes when read-only graph props change", async () => {
    const initialNodes: GraphNode[] = [
      { id: "run-trigger", title: "Run Trigger", type: "locus/trigger", x: 80, y: 120 },
    ];
    const nextNodes: GraphNode[] = [
      { id: "run-trigger", title: "Run Trigger", type: "locus/trigger", x: 80, y: 120 },
      { id: "run-agent", title: "Agent", type: "locus/agent", x: 360, y: 120 },
      { id: "run-output", title: "Output", type: "locus/output", x: 680, y: 120 },
    ];
    const nextLinks: GraphLink[] = [
      { from: "run-trigger", to: "run-agent", from_port: "out", to_port: "in" },
      { from: "run-agent", to: "run-output", from_port: "response", to_port: "in" },
    ];

    const { rerender } = render(<ReactFlowCanvas nodes={initialNodes} links={[]} readOnly />);

    await waitFor(() => {
      const latestProps = reactFlowPropsSpy.mock.calls.at(-1)?.[0] as { nodes?: Array<{ id: string }> };
      expect(latestProps.nodes?.map((node) => node.id)).toEqual(["run-trigger"]);
    });

    rerender(<ReactFlowCanvas nodes={nextNodes} links={nextLinks} readOnly />);

    await waitFor(() => {
      const latestProps = reactFlowPropsSpy.mock.calls.at(-1)?.[0] as { nodes?: Array<{ id: string }>; edges?: Array<{ source: string; target: string }> };
      expect(latestProps.nodes?.map((node) => node.id)).toEqual(["run-trigger", "run-agent", "run-output"]);
      expect(latestProps.edges).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ source: "run-trigger", target: "run-agent" }),
        ]),
      );
    });
  });

  it("preserves read-only selection changes in the rendered graph state", async () => {
    const readOnlyNodes: GraphNode[] = [
      { id: "run-trigger", title: "Run Trigger", type: "locus/trigger", x: 80, y: 120 },
      { id: "run-agent", title: "Agent", type: "locus/agent", x: 360, y: 120 },
    ];
    const readOnlyLinks: GraphLink[] = [
      { from: "run-trigger", to: "run-agent", from_port: "out", to_port: "in" },
    ];

    render(<ReactFlowCanvas nodes={readOnlyNodes} links={readOnlyLinks} readOnly />);

    fireEvent.click(screen.getByRole("button", { name: /select run-trigger/i }));
    fireEvent.click(screen.getByRole("button", { name: /select first edge/i }));

    await waitFor(() => {
      const latestProps = reactFlowPropsSpy.mock.calls.at(-1)?.[0] as {
        nodes?: Array<{ id: string; selected?: boolean }>;
        edges?: Array<{ id: string; selected?: boolean }>;
      };

      expect(latestProps.nodes?.find((node) => node.id === "run-trigger")?.selected).toBe(true);
      expect(latestProps.edges?.find((edge) => edge.id === "run-trigger:out->run-agent:in:0")?.selected).toBe(true);
    });
  });
});