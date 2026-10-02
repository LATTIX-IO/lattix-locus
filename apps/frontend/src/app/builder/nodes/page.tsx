"use client";

import Link from "next/link";
import { NodeFieldForm } from "@/components/node-field-form";
import { getNodeDefinitions, type NodeFieldSpec } from "@/lib/api";
import { locusNodeTemplates, type LocusNodeTemplate } from "@/lib/locus-node-catalog";
import { useEffect, useMemo, useState } from "react";

type CustomPort = {
  name: string;
  type: string;
  location: "left" | "right" | "top" | "bottom";
};

type CustomField = {
  key: string;
  type: "text" | "number" | "boolean" | "select";
  defaultValue: string;
};

const emptyTemplate: LocusNodeTemplate = {
  id: "",
  key: "locus/trigger",
  name: "",
  category: "Core",
  description: "",
  color: "#6ca0ff",
};

export default function NodeLibraryPage() {
  const [nodeTemplates, setNodeTemplates] = useState<LocusNodeTemplate[]>(locusNodeTemplates);
  const [selectedNode, setSelectedNode] = useState<string>(locusNodeTemplates[0]?.id ?? "");
  const [nodeInputs, setNodeInputs] = useState<Record<string, NodeFieldSpec[]>>({});

  useEffect(() => {
    let cancelled = false;

    async function loadTemplates() {
      const response = await getNodeDefinitions();
      if (cancelled || response.length === 0) {
        return;
      }

      const mapped: LocusNodeTemplate[] = response
        .filter((node) => node.type_key.startsWith("locus/"))
        .map((node) => {
          const fallback = locusNodeTemplates.find((template) => template.key === node.type_key);
          return {
            id: node.type_key,
            key: node.type_key as `locus/${string}`,
            name: node.title ?? fallback?.name ?? node.type_key.replace("locus/", ""),
            category: (node.category as LocusNodeTemplate["category"]) ?? fallback?.category ?? "Core",
            description: node.description,
            color: node.color ?? fallback?.color ?? "#6ca0ff",
          };
        });

      if (mapped.length > 0) {
        setNodeTemplates(mapped);
        setSelectedNode((current) => (mapped.some((item) => item.id === current) ? current : mapped[0].id));
        const inputsByKey: Record<string, NodeFieldSpec[]> = {};
        for (const node of response) {
          if (node.inputs && node.inputs.length > 0) {
            inputsByKey[node.type_key] = node.inputs;
          }
        }
        setNodeInputs(inputsByKey);
      }
    }

    void loadTemplates();

    return () => {
      cancelled = true;
    };
  }, []);

  const [nodeName, setNodeName] = useState("custom/rag-retriever");
  const [category, setCategory] = useState<"RAG" | "API" | "Guardrails" | "Agent" | "Utility">("RAG");
  const [description, setDescription] = useState("Retrieve and rank context chunks from knowledge sources.");

  const [inputPorts, setInputPorts] = useState<CustomPort[]>([
    { name: "query", type: "text", location: "left" },
  ]);
  const [outputPorts, setOutputPorts] = useState<CustomPort[]>([
    { name: "context", type: "json", location: "right" },
  ]);
  const [fields, setFields] = useState<CustomField[]>([
    { key: "topK", type: "number", defaultValue: "5" },
    { key: "index", type: "text", defaultValue: "default-index" },
  ]);

  const selectedTemplate = nodeTemplates.find((template) => template.id === selectedNode) ?? nodeTemplates[0] ?? emptyTemplate;

  const configPreview = useMemo(
    () => ({
      name: nodeName,
      category,
      description: description || selectedTemplate.description,
      basedOnTemplate: {
        id: selectedTemplate.id,
        key: selectedTemplate.key,
        name: selectedTemplate.name,
      },
      ports: {
        inputs: inputPorts,
        outputs: outputPorts,
      },
      fields,
      templates: {
        rag: category === "RAG",
        apiConnection: category === "API",
        guardrails: category === "Guardrails",
      },
    }),
    [category, description, fields, inputPorts, nodeName, outputPorts, selectedTemplate],
  );

  function updateInputPort(index: number, patch: Partial<CustomPort>) {
    setInputPorts((ports) => ports.map((port, i) => (i === index ? { ...port, ...patch } : port)));
  }

  function updateOutputPort(index: number, patch: Partial<CustomPort>) {
    setOutputPorts((ports) => ports.map((port, i) => (i === index ? { ...port, ...patch } : port)));
  }

  function updateField(index: number, patch: Partial<CustomField>) {
    setFields((current) => current.map((field, i) => (i === index ? { ...field, ...patch } : field)));
  }

  return (
    <section className="flex min-h-0 flex-col space-y-4 xl:h-full">
      <header>
        <h1 className="text-2xl font-semibold">Node Library</h1>
        <p className="fx-muted">
          Curated Locus node kit only — no default legacy nodes — with reusable templates for agents and workflows.
        </p>
      </header>

      <div className="grid min-h-0 gap-4 xl:flex-1 xl:grid-cols-[360px_1fr]">
        <aside className="fx-panel flex min-h-[calc(100vh-14rem)] flex-col p-4 xl:min-h-0">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide">Locus Node Kit</h2>
          <div
            aria-label="Available node templates"
            className="min-h-0 flex-1 overflow-y-auto border border-[var(--fx-border)]"
          >
            <ul className="text-sm">
              {nodeTemplates.length === 0 ? (
                <li className="px-3 py-3 text-xs fx-muted">No node templates available.</li>
              ) : nodeTemplates.map((template) => (
                <li key={template.id}>
                  <div className={`border-b border-[var(--fx-border)] ${template.id === selectedNode ? "fx-nav-active" : ""}`}>
                    <button
                      onClick={() => {
                        setSelectedNode(template.id);
                        setNodeName(template.key);
                        setDescription(template.description);
                      }}
                      className="w-full px-3 py-2 text-left hover:bg-[var(--fx-nav-hover)]"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span>{template.name}</span>
                        <span className="fx-muted text-[10px] uppercase">{template.category}</span>
                      </div>
                      <p className="fx-muted mt-1 text-xs">{template.key}</p>
                    </button>
                    <div className="flex items-center justify-between px-3 pb-2 text-[10px]">
                      <span className="font-mono text-[var(--foreground)]">{template.id}</span>
                      <div className="flex items-center gap-2">
                        <Link href={`/builder/nodes/${template.id}`} className="fx-muted hover:underline">
                          Open
                        </Link>
                      </div>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </div>
          <p className="fx-muted mt-2 text-xs">Showing {nodeTemplates.length} reusable node templates in the Locus kit.</p>
          <p className="mt-2 text-xs text-[var(--foreground)]">
            Node definitions are currently read-only. The custom-node publishing workflow stays hidden until secure backend lifecycle support is implemented.
          </p>
        </aside>

        <div className="space-y-4">
          <div className="fx-panel p-4">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold uppercase tracking-wide">{selectedTemplate.name} — configurable inputs</h2>
              <span className="fx-muted text-[10px] uppercase">{selectedTemplate.key}</span>
            </div>
            <p className="fx-muted mb-3 text-xs">
              Declarative input schema. The studio renders this node&apos;s config panel from these specs —
              edit once here and every workflow gets the same controls.
            </p>
            <NodeFieldForm fields={nodeInputs[selectedTemplate.key] ?? []} readOnly />
          </div>

          <div className="fx-panel p-4">
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide">Custom node builder</h2>
            <div className="grid gap-3 md:grid-cols-2">
              <label className="block text-sm">
                Node key
                <input className="fx-field mt-1 w-full px-2 py-2 text-sm" value={nodeName} onChange={(e) => setNodeName(e.target.value)} />
              </label>
              <label className="block text-sm">
                Category
                <select
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={category}
                  onChange={(e) => setCategory(e.target.value as "RAG" | "API" | "Guardrails" | "Agent" | "Utility")}
                >
                  <option>RAG</option>
                  <option>API</option>
                  <option>Guardrails</option>
                  <option>Agent</option>
                  <option>Utility</option>
                </select>
              </label>
              <label className="block text-sm md:col-span-2">
                Description
                <input className="fx-field mt-1 w-full px-2 py-2 text-sm" value={description} onChange={(e) => setDescription(e.target.value)} />
              </label>
            </div>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <div className="fx-panel p-4">
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-semibold">Input ports</h3>
                <button
                  onClick={() => setInputPorts((ports) => [...ports, { name: "input", type: "any", location: "left" }])}
                  className="fx-btn-secondary px-2 py-1 text-xs"
                >
                  Add input
                </button>
              </div>
              <div className="space-y-2">
                {inputPorts.map((port, index) => (
                  <div key={`in-${index}`} className="grid gap-2 border border-[var(--fx-border)] p-2 md:grid-cols-3">
                    <input
                      className="fx-field px-2 py-1 text-xs"
                      value={port.name}
                      onChange={(e) => updateInputPort(index, { name: e.target.value })}
                      placeholder="name"
                    />
                    <input
                      className="fx-field px-2 py-1 text-xs"
                      value={port.type}
                      onChange={(e) => updateInputPort(index, { type: e.target.value })}
                      placeholder="type"
                    />
                    <select
                      className="fx-field px-2 py-1 text-xs"
                      value={port.location}
                      onChange={(e) => updateInputPort(index, { location: e.target.value as CustomPort["location"] })}
                    >
                      <option>left</option>
                      <option>right</option>
                      <option>top</option>
                      <option>bottom</option>
                    </select>
                  </div>
                ))}
              </div>
            </div>

            <div className="fx-panel p-4">
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-semibold">Output ports</h3>
                <button
                  onClick={() => setOutputPorts((ports) => [...ports, { name: "output", type: "any", location: "right" }])}
                  className="fx-btn-secondary px-2 py-1 text-xs"
                >
                  Add output
                </button>
              </div>
              <div className="space-y-2">
                {outputPorts.map((port, index) => (
                  <div key={`out-${index}`} className="grid gap-2 border border-[var(--fx-border)] p-2 md:grid-cols-3">
                    <input
                      className="fx-field px-2 py-1 text-xs"
                      value={port.name}
                      onChange={(e) => updateOutputPort(index, { name: e.target.value })}
                      placeholder="name"
                    />
                    <input
                      className="fx-field px-2 py-1 text-xs"
                      value={port.type}
                      onChange={(e) => updateOutputPort(index, { type: e.target.value })}
                      placeholder="type"
                    />
                    <select
                      className="fx-field px-2 py-1 text-xs"
                      value={port.location}
                      onChange={(e) => updateOutputPort(index, { location: e.target.value as CustomPort["location"] })}
                    >
                      <option>left</option>
                      <option>right</option>
                      <option>top</option>
                      <option>bottom</option>
                    </select>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="fx-panel p-4">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-sm font-semibold">Node fields</h3>
              <button
                onClick={() => setFields((current) => [...current, { key: "field", type: "text", defaultValue: "" }])}
                className="fx-btn-secondary px-2 py-1 text-xs"
              >
                Add field
              </button>
            </div>
            <div className="space-y-2">
              {fields.map((field, index) => (
                <div key={`${field.key}-${index}`} className="grid gap-2 border border-[var(--fx-border)] p-2 md:grid-cols-3">
                  <input
                    className="fx-field px-2 py-1 text-xs"
                    value={field.key}
                    onChange={(e) => updateField(index, { key: e.target.value })}
                    placeholder="field key"
                  />
                  <select
                    className="fx-field px-2 py-1 text-xs"
                    value={field.type}
                    onChange={(e) => updateField(index, { type: e.target.value as CustomField["type"] })}
                  >
                    <option>text</option>
                    <option>number</option>
                    <option>boolean</option>
                    <option>select</option>
                  </select>
                  <input
                    className="fx-field px-2 py-1 text-xs"
                    value={field.defaultValue}
                    onChange={(e) => updateField(index, { defaultValue: e.target.value })}
                    placeholder="default"
                  />
                </div>
              ))}
            </div>
          </div>

          <div className="fx-panel p-4">
            <h3 className="mb-2 text-sm font-semibold">Configuration preview</h3>
            <pre className="fx-field max-h-72 overflow-auto p-3 text-xs">{JSON.stringify(configPreview, null, 2)}</pre>
            <div className="mt-3 rounded border border-[color-mix(in_srgb,var(--fx-warning)_42%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-warning)_10%,transparent)] px-3 py-3 text-sm text-[var(--foreground)]">
              Custom node save and publish actions are intentionally disabled until the backend supports authenticated node lifecycle management.
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
