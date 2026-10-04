import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  addKnowledgeDocument: vi.fn(),
  createKnowledgeCollection: vi.fn(),
  deleteKnowledgeCollection: vi.fn(),
  getKnowledgeCollections: vi.fn(),
  getKnowledgeVectorStores: vi.fn(),
  getMemoryLayers: vi.fn(),
  getPlatformHealthDetails: vi.fn(),
  searchKnowledgeCollection: vi.fn(),
}));

vi.mock("@/lib/api", () => api);
vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children: ReactNode; href: string }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

import KnowledgePage from "@/app/library/knowledge/page";

const COLLECTION = {
  id: "kb-1",
  name: "Runbooks",
  description: "",
  created_at: "2026-10-01",
  document_count: 0,
  chunk_count: 0,
  vector_store_id: "platform",
};

function layers(longTermReady: boolean) {
  return [
    { id: "short_term", name: "Short-term memory", backend: "Redis", scope: "session", enabled: false, healthy: false, stats: {} },
    {
      id: "long_term",
      name: "Long-term memory",
      backend: "Postgres + pgvector",
      scope: "durable",
      enabled: longTermReady,
      healthy: longTermReady,
      stats: { vector_search: longTermReady, embedding_model: "nomic-embed-text" },
    },
  ];
}

function store(ready: boolean) {
  return [
    {
      id: "platform",
      name: "Platform vector store (pgvector)",
      kind: "builtin",
      ready,
      status: ready ? "configured" : "unavailable",
      embedding_model: "nomic-embed-text",
      note: ready ? "" : "Long-term memory store (Postgres + pgvector + embeddings) is not available.",
    },
  ];
}

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset());
  api.getKnowledgeCollections.mockResolvedValue([COLLECTION]);
  api.getMemoryLayers.mockResolvedValue(layers(false));
  api.getKnowledgeVectorStores.mockResolvedValue(store(false));
  api.getPlatformHealthDetails.mockResolvedValue({ long_term_memory: "disabled", long_term_memory_reason: "POSTGRES_DSN is not set" });
});

describe("Library → Knowledge", () => {
  it("makes the not-configured state actionable with a Set up path to Settings", async () => {
    render(<KnowledgePage />);

    expect(await screen.findByText(/documents cannot be indexed or searched yet/i)).toBeInTheDocument();
    expect(screen.getByText(/postgres_dsn is not set/i)).toBeInTheDocument();
    const setUp = screen.getAllByRole("link", { name: "Set up" });
    expect(setUp.length).toBeGreaterThan(0);
    setUp.forEach((link) => expect(link).toHaveAttribute("href", "/settings?section=memory"));
    expect(screen.getByRole("button", { name: /index document/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^search$/i })).toBeDisabled();
    // Configuration moved to Settings: no memory-layer panel here.
    expect(screen.queryByRole("heading", { name: /memory layers/i })).not.toBeInTheDocument();
  });

  it("indexes and searches when long-term memory is ready", async () => {
    api.getMemoryLayers.mockResolvedValue(layers(true));
    api.getKnowledgeVectorStores.mockResolvedValue(store(true));
    api.getPlatformHealthDetails.mockResolvedValue({ long_term_memory: "connected" });
    render(<KnowledgePage />);

    expect(await screen.findByRole("button", { name: /index document/i })).toBeEnabled();
    expect(screen.queryByText(/documents cannot be indexed/i)).not.toBeInTheDocument();
  });

  it("shows a failed read as an error with Retry instead of an empty state", async () => {
    api.getMemoryLayers.mockRejectedValueOnce(new Error("Request failed (500)"));
    render(<KnowledgePage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/request failed \(500\)/i);
    expect(screen.queryByText(/documents cannot be indexed/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(await screen.findByText(/documents cannot be indexed or searched yet/i)).toBeInTheDocument();
  });
});
