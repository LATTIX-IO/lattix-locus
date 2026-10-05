import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock fetch globally
const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

/** Minimal fetch body: yields each SSE chunk, then closes. */
function sseBody(chunks: string[]) {
  const encoder = new TextEncoder();
  let index = 0;
  return {
    getReader: () => ({
      read: async () =>
        index < chunks.length
          ? { done: false, value: encoder.encode(chunks[index++]) }
          : { done: true, value: undefined },
    }),
  };
}

// Reset modules so each test gets a fresh copy of api.ts state
beforeEach(() => {
  fetchMock.mockReset();
  vi.unstubAllEnvs();
  vi.resetModules();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("getApiBase", () => {
  it("returns NEXT_PUBLIC_API_BASE_URL on client side", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://custom:9000");
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => [] });

    const { getPublishedWorkflows } = await import("@/lib/api");
    await getPublishedWorkflows();

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("http://custom:9000"),
      expect.any(Object),
    );
  });

  it("defaults to the local gateway api path on client side", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => [] });

    const { getPublishedWorkflows } = await import("@/lib/api");
    await getPublishedWorkflows();

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/workflows/published"),
      expect.any(Object),
    );
  });
});

describe("reads surface failures (no silent fallbacks)", () => {
  function failed(status: number) {
    return { ok: false, status, text: async () => `{"detail":"status ${status}"}` };
  }

  it("returns parsed JSON on success", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => [{ id: "wf-1", name: "Test" }],
    });

    const { getPublishedWorkflows } = await import("@/lib/api");
    const result = await getPublishedWorkflows();

    expect(result).toEqual([{ id: "wf-1", name: "Test" }]);
  });

  it("throws on a failed list read instead of returning an empty list", async () => {
    fetchMock.mockResolvedValue(failed(500));

    const { getArtifacts } = await import("@/lib/api");
    await expect(getArtifacts()).rejects.toThrow(/Request failed \(500\)/);
  });

  it("never invents platform settings, a security policy or a node catalog", async () => {
    fetchMock.mockResolvedValue(failed(500));

    const { getPlatformSettings, getPlatformSecurityPolicy, getNodeDefinitions } = await import("@/lib/api");
    await expect(getPlatformSettings()).rejects.toThrow(/500/);
    await expect(getPlatformSecurityPolicy()).rejects.toThrow(/500/);
    await expect(getNodeDefinitions()).rejects.toThrow(/500/);
  });

  it("never reports a graph as valid when validation fails", async () => {
    fetchMock.mockResolvedValue(failed(503));

    const { validateGraph } = await import("@/lib/api");
    await expect(validateGraph({ nodes: [], links: [] })).rejects.toThrow();
  });

  it("treats 404 as 'not found' for single-item reads", async () => {
    fetchMock.mockResolvedValue(failed(404));

    const { getArtifact, getWorkflowDefinition } = await import("@/lib/api");
    await expect(getArtifact("missing")).resolves.toBeNull();
    await expect(getWorkflowDefinition("missing")).resolves.toBeNull();
  });

  it("reads 401 from /auth/session as signed out, and throws on other failures", async () => {
    fetchMock.mockResolvedValueOnce(failed(401));
    const first = await import("@/lib/api");
    await expect(first.getOperatorSession()).resolves.toMatchObject({ authenticated: false });

    vi.resetModules();
    fetchMock.mockResolvedValue(failed(500));
    const second = await import("@/lib/api");
    await expect(second.getOperatorSession()).rejects.toThrow(/500/);
  });
});

describe("required core fetches", () => {
  it("creates workflow runs with auth-aware headers and same-origin credentials", async () => {
    vi.stubEnv("NEXT_PUBLIC_LOCUS_ACTOR", "frontend-user");
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ id: "run-1", status: "Running" }),
    });

    const { createWorkflowRun } = await import("@/lib/api");
    const result = await createWorkflowRun({ session_kind: "task", prompt: "Hello" });

    expect(result).toEqual({ id: "run-1", status: "Running" });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/workflow-runs"),
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        headers: expect.objectContaining({
          "Content-Type": "application/json",
          "x-locus-actor": "frontend-user",
        }),
      }),
    );
  });

  it("opens run streams with auth-aware headers and same-origin credentials", async () => {
    vi.stubEnv("NEXT_PUBLIC_LOCUS_ACTOR", "frontend-user");
    fetchMock.mockResolvedValue({ ok: false, body: null });

    const { streamWorkflowRun } = await import("@/lib/api");
    streamWorkflowRun("run-1", { onMessage: vi.fn(), onError: vi.fn() });
    await Promise.resolve();

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/workflow-runs/run-1/stream"),
      expect.objectContaining({
        method: "GET",
        credentials: "include",
        headers: expect.objectContaining({
          "x-locus-actor": "frontend-user",
        }),
      }),
    );
  });

  it("reports a run stream that closes without an end frame as dropped, never as ended", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      body: sseBody([
        'data: {"id":"s-1","type":"delta","createdAt":"t","payload":{"text":"partial"}}\n\n',
      ]),
    });
    const onMessage = vi.fn();
    const onEnd = vi.fn();
    const onDisconnect = vi.fn();

    const { streamWorkflowRun } = await import("@/lib/api");
    streamWorkflowRun("run-1", { onMessage, onEnd, onDisconnect });

    await vi.waitFor(() => expect(onDisconnect).toHaveBeenCalledWith({ reason: "dropped" }));
    expect(onMessage).toHaveBeenCalledTimes(1);
    expect(onEnd).not.toHaveBeenCalled();
  });

  it("delivers the terminal end frame once and resumes with the after cursor", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      body: sseBody([
        'data: {"id":"s-2","type":"final","createdAt":"t","payload":{"text":"done"}}\n\n',
        'event: end\ndata: {"id":"end-1","type":"end","createdAt":"t","payload":{"reason":"complete","status":"Done","terminal":true}}\n\n',
      ]),
    });
    const onMessage = vi.fn();
    const onEnd = vi.fn();
    const onDisconnect = vi.fn();

    const { streamWorkflowRun } = await import("@/lib/api");
    streamWorkflowRun("run-1", { onMessage, onEnd, onDisconnect }, { after: "s-1" });

    await vi.waitFor(() => expect(onEnd).toHaveBeenCalledTimes(1));
    expect(onEnd).toHaveBeenCalledWith({ reason: "complete", status: "Done", terminal: true });
    expect(onDisconnect).not.toHaveBeenCalled();
    expect(onMessage).toHaveBeenCalledWith(expect.objectContaining({ id: "s-2", type: "final" }));
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/workflow-runs/run-1/stream?after=s-1"),
      expect.any(Object),
    );
  });

  it("treats a stream_closed rotation as a non-terminal disconnect carrying the cursor", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      body: sseBody([
        'event: stream_closed\ndata: {"id":"c-1","type":"stream_closed","createdAt":"t","payload":{"reason":"timeout","reconnect":true,"after":"s-9"}}\n\n',
      ]),
    });
    const onEnd = vi.fn();
    const onDisconnect = vi.fn();

    const { streamWorkflowRun } = await import("@/lib/api");
    streamWorkflowRun("run-1", { onMessage: vi.fn(), onEnd, onDisconnect });

    await vi.waitFor(() => expect(onDisconnect).toHaveBeenCalledWith({ reason: "timeout", after: "s-9" }));
    expect(onEnd).not.toHaveBeenCalled();
  });

  it("rejects run event streams that close without a lifecycle frame", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      body: sseBody(['event: run_status\ndata: {"status":"Running"}\n\n']),
    });

    const { streamWorkflowRunEvents } = await import("@/lib/api");
    const { RunStreamInterruptedError } = await import("@/lib/run-stream");

    await expect(
      streamWorkflowRunEvents("run-1", { signal: new AbortController().signal }),
    ).rejects.toBeInstanceOf(RunStreamInterruptedError);
  });

  it("resolves run event streams as terminal only on the end frame", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      body: sseBody(['event: end\ndata: {"reason":"terminal","status":"Done","terminal":true}\n\n']),
    });
    fetchMock.mockResolvedValueOnce({
      ok: true,
      body: sseBody(['event: stream_closed\ndata: {"reason":"timeout","reconnect":true,"after":""}\n\n']),
    });

    const { streamWorkflowRunEvents } = await import("@/lib/api");
    const signal = new AbortController().signal;

    await expect(streamWorkflowRunEvents("run-1", { signal })).resolves.toBe("terminal");
    await expect(streamWorkflowRunEvents("run-1", { signal })).resolves.toBe("timeout");
  });

  it("throws on network error for inbox reads", async () => {
    fetchMock.mockRejectedValue(new Error("Network failure"));

    const { getInbox } = await import("@/lib/api");

    await expect(getInbox()).rejects.toThrow(/Network failure/);
  });

  it("normalizes run kinds returned by workflow runs", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => [{
        id: "run-1",
        title: "Quarterly review",
        status: "Done",
        updatedAt: "2026-04-04T00:00:00Z",
        progressLabel: "Completed",
        kind: "chat",
      }],
    });

    const { getWorkflowRuns } = await import("@/lib/api");
    const result = await getWorkflowRuns();

    expect(result[0]?.kind).toBe("chat");
  });

  it("invalidates workflow run and inbox caches after archiving a run", async () => {
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{
          id: "run-1",
          title: "Quarterly review",
          status: "Running",
          updatedAt: "2026-04-04T00:00:00Z",
          progressLabel: "Responding",
          kind: "chat",
        }],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{
          id: "inbox-1",
          runId: "run-1",
          runName: "Quarterly review",
          artifactType: "brief",
          reason: "Needs review",
          queue: "Needs Review",
        }],
      })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true }) })
      .mockResolvedValueOnce({ ok: true, json: async () => [] })
      .mockResolvedValueOnce({ ok: true, json: async () => [] });

    const { archiveWorkflowRun, getInbox, getWorkflowRuns } = await import("@/lib/api");

    await getWorkflowRuns();
    await getInbox();
    await archiveWorkflowRun("run-1");
    await getWorkflowRuns();
    await getInbox();

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/workflow-runs/run-1/archive"),
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });

  it("throws when runtime providers cannot be loaded", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 503,
      text: async () => "Service unavailable",
    });

    const { getRuntimeProviders } = await import("@/lib/api");

    await expect(getRuntimeProviders()).rejects.toThrow(/503/);
  });

  it("posts graph validation requests with the locus schema version", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ valid: true, issues: [] }),
    });

    const { validateGraph } = await import("@/lib/api");
    const result = await validateGraph({ nodes: [], links: [] });

    expect(result).toEqual({ valid: true, issues: [] });
    const callBody = fetchMock.mock.calls[0]?.[1]?.body;
    expect(typeof callBody).toBe("string");
    expect(JSON.parse(callBody as string)).toEqual({
      schema_version: "locus-graph/1.0",
      nodes: [],
      links: [],
    });
  });

  it("posts playbook collaboration join requests through the strict collaboration endpoint", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        ok: true,
        session: {
          id: "playbook:pb-1",
          entity_type: "playbook",
          entity_id: "pb-1",
          graph_json: { nodes: [], links: [] },
          version: 1,
          updated_at: "2026-04-06T00:00:00Z",
          participants: [],
        },
        participant: {
          user_id: "tester",
          display_name: "Tester",
          role: "editor",
        },
      }),
    });

    const { joinCollaborationSession } = await import("@/lib/api");
    const result = await joinCollaborationSession({
      entity_type: "playbook",
      entity_id: "pb-1",
      display_name: "Tester",
    });

    expect(result.ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/collab/sessions/join"),
      expect.objectContaining({ method: "POST" }),
    );
    const callBody = fetchMock.mock.calls[0]?.[1]?.body;
    expect(JSON.parse(callBody as string)).toEqual({
      entity_type: "playbook",
      entity_id: "pb-1",
      display_name: "Tester",
    });
  });

  it("posts collaboration sync payloads and preserves conflict responses", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        ok: false,
        conflict: true,
        message: "version conflict",
        version: 3,
        graph_json: { nodes: [{ id: "n1", title: "Node 1", type: "locus/trigger", x: 0, y: 0 }], links: [] },
        updated_at: "2026-04-06T00:00:00Z",
      }),
    });

    const { syncCollaborationSession } = await import("@/lib/api");
    const result = await syncCollaborationSession("playbook:pb-1", {
      base_version: 2,
      graph_json: { nodes: [{ id: "n1", title: "Node 1", type: "locus/trigger", x: 0, y: 0 }], links: [] },
    });

    expect(result.conflict).toBe(true);
    expect(result.version).toBe(3);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/collab/sessions/playbook%3Apb-1/sync"),
      expect.objectContaining({ method: "POST" }),
    );
  });
});

describe("strictFetch", () => {
  it("throws on non-ok response", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 422,
      text: async () => "Validation error",
    });

    const { saveWorkflowDefinition } = await import("@/lib/api");

    await expect(saveWorkflowDefinition({ id: "test" })).rejects.toThrow(/422/);
  });

  it("returns parsed JSON on success", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true }),
    });

    const { saveWorkflowDefinition } = await import("@/lib/api");
    const result = await saveWorkflowDefinition({ id: "test" });

    expect(result).toEqual({ ok: true });
  });

  it("throws when platform settings cannot be saved", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 503,
      text: async () => "Service unavailable",
    });

    const { savePlatformSettings } = await import("@/lib/api");

    await expect(savePlatformSettings({ require_human_approval: true })).rejects.toThrow(/503/);
  });

  it("throws when integrations cannot be saved", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 400,
      text: async () => "Rejected by policy",
    });

    const { saveIntegration } = await import("@/lib/api");

    await expect(saveIntegration({ name: "CRM" })).rejects.toThrow(/400/);
  });

  it("throws when node deletion is unsupported", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 501,
      text: async () => "Node definitions are currently read-only",
    });

    const { deleteNodeDefinition } = await import("@/lib/api");

    await expect(deleteNodeDefinition("locus/router")).rejects.toThrow(/501/);
  });

  it("invalidates a cached anonymous operator session after successful login", async () => {
    vi.resetModules();
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          authenticated: false,
          actor: "anonymous",
          principal_id: null,
          principal_type: "anonymous",
          display_name: "Anonymous",
          subject: null,
          email: null,
          preferred_username: null,
          auth_mode: "oidc",
          provider: "casdoor",
          roles: [],
          capabilities: { can_admin: false, can_builder: false },
          allowed_modes: ["user"],
          default_mode: "user",
          oidc: { configured: true, issuer: "http://127.0.0.1:8081", audience: "", provider: "casdoor" },
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ok: true, authenticated: true, provider: "casdoor", mode: "password" }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          authenticated: true,
          actor: "jpbooth",
          principal_id: "built-in/jpbooth",
          principal_type: "user",
          display_name: "jpbooth",
          subject: "built-in/jpbooth",
          email: "jpbooth@example.com",
          preferred_username: "jpbooth",
          auth_mode: "oidc",
          provider: "casdoor",
          roles: ["member"],
          capabilities: { can_admin: false, can_builder: false },
          allowed_modes: ["user"],
          default_mode: "user",
          oidc: { configured: true, issuer: "http://127.0.0.1:8081", audience: "", provider: "casdoor" },
        }),
      });

    const { getOperatorSession, loginWithLocalPassword } = await import("@/lib/api");

    const anonymousSession = await getOperatorSession();
    expect(anonymousSession.authenticated).toBe(false);

    await loginWithLocalPassword({ username: "jpbooth", password: "PhenoiX1!" });

    const authenticatedSession = await getOperatorSession();
    expect(authenticatedSession.authenticated).toBe(true);
    expect(authenticatedSession.actor).toBe("jpbooth");
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});

describe("retry logic", () => {
  it("retries on 502 and succeeds", async () => {
    fetchMock
      .mockResolvedValueOnce({ ok: false, status: 502 })
      .mockResolvedValueOnce({ ok: true, json: async () => [] });

    const { getPublishedWorkflows } = await import("@/lib/api");
    const result = await getPublishedWorkflows();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result).toEqual([]);
  });

  it("retries on network error and succeeds", async () => {
    fetchMock
      .mockRejectedValueOnce(new Error("ECONNREFUSED"))
      .mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true }) });

    const { saveWorkflowDefinition } = await import("@/lib/api");
    const result = await saveWorkflowDefinition({ id: "test" });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result).toEqual({ ok: true });
  });
});

describe("onApiStatusChange", () => {
  it("notifies listeners on connectivity change", async () => {
    const { onApiStatusChange, getPublishedWorkflows } = await import("@/lib/api");
    const listener = vi.fn();
    const unsub = onApiStatusChange(listener);

    // Force a network error to set connected=false
    fetchMock.mockRejectedValue(new Error("offline"));
    // The failure surfaces to the caller (no silent fallback) and the banner hears it.
    await expect(getPublishedWorkflows()).rejects.toThrow("offline");

    expect(listener).toHaveBeenCalledWith(false);

    // Restore connectivity
    fetchMock.mockResolvedValue({ ok: true, json: async () => [] });
    await getPublishedWorkflows();

    expect(listener).toHaveBeenCalledWith(true);

    unsub();
  });
});

describe("identity headers", () => {
  it("omits x-locus-actor when no actor is configured", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => [] });

    const { getPublishedWorkflows } = await import("@/lib/api");
    await getPublishedWorkflows();

    const callHeaders = fetchMock.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(callHeaders["x-locus-actor"]).toBeUndefined();
  });

  it("includes x-locus-actor only when explicitly configured", async () => {
    vi.stubEnv("NEXT_PUBLIC_LOCUS_ACTOR", "frontend-user");
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => [] });

    const { getPublishedWorkflows } = await import("@/lib/api");
    await getPublishedWorkflows();

    const callHeaders = fetchMock.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(callHeaders["x-locus-actor"]).toBe("frontend-user");
  });

  it("never includes Authorization from browser env", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => [] });

    const { getPublishedWorkflows } = await import("@/lib/api");
    await getPublishedWorkflows();

    const callHeaders = fetchMock.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(callHeaders["Authorization"]).toBeUndefined();
  });

  it("forwards incoming cookie headers during server-side protected fetches", async () => {
    vi.resetModules();
    vi.doMock("next/headers", () => ({
      headers: async () => new Headers({ cookie: "locus_operator_session=test-cookie" }),
    }));

    const originalWindow = globalThis.window;
    Object.defineProperty(globalThis, "window", {
      value: undefined,
      configurable: true,
      writable: true,
    });

    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => [] });

    try {
      const { getInbox } = await import("@/lib/api");
      await getInbox();

      const callHeaders = fetchMock.mock.calls[0]?.[1]?.headers as Record<string, string>;
      expect(callHeaders.cookie).toBe("locus_operator_session=test-cookie");
    } finally {
      Object.defineProperty(globalThis, "window", {
        value: originalWindow,
        configurable: true,
        writable: true,
      });
      vi.doUnmock("next/headers");
      vi.resetModules();
    }
  });
});
