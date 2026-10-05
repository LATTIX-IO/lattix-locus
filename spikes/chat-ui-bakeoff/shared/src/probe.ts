/**
 * In-page measurement hooks shared by both variants, so the bench reads identical signals:
 * long tasks, CSP violations, run inputs the library sent to the agent, and confirm calls.
 */

export type RunInputRecord = {
  runId: string;
  tools: string[];
  contextEntries: number;
  forwardedPropsKeys: string[];
  messages: number;
  hasResume: boolean;
  resume?: unknown;
};

export type BenchState = {
  longTasks: { start: number; duration: number }[];
  cspViolations: { directive: string; blockedURI: string; sample: string }[];
  runInputs: RunInputRecord[];
  confirmCalls: unknown[];
  marks: Record<string, number>;
  errors: string[];
};

declare global {
  interface Window {
    __bench: BenchState;
  }
}

export function installProbe(): BenchState {
  if (window.__bench) return window.__bench;
  const state: BenchState = { longTasks: [], cspViolations: [], runInputs: [], confirmCalls: [], marks: {}, errors: [] };
  window.__bench = state;
  try {
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) state.longTasks.push({ start: e.startTime, duration: e.duration });
    }).observe({ type: "longtask", buffered: true });
  } catch {
    // longtask is Chromium-only; the bench runs Chromium.
  }
  document.addEventListener("securitypolicyviolation", (e) => {
    state.cspViolations.push({ directive: e.violatedDirective, blockedURI: e.blockedURI, sample: e.sample });
  });
  window.addEventListener("error", (e) => state.errors.push(String(e.message)));
  window.addEventListener("unhandledrejection", (e) => state.errors.push(String((e as PromiseRejectionEvent).reason)));
  return state;
}

export function mark(name: string) {
  window.__bench.marks[name] ??= performance.now();
}

export function recordRunInput(input: {
  runId: string;
  tools?: { name: string }[] | undefined;
  context?: unknown[] | undefined;
  forwardedProps?: Record<string, unknown> | undefined;
  messages?: unknown[] | undefined;
  resume?: unknown;
}) {
  window.__bench.runInputs.push({
    runId: input.runId,
    tools: (input.tools ?? []).map((t) => t.name),
    contextEntries: input.context?.length ?? 0,
    forwardedPropsKeys: Object.keys(input.forwardedProps ?? {}),
    messages: input.messages?.length ?? 0,
    hasResume: Array.isArray(input.resume) && input.resume.length > 0,
    resume: input.resume,
  });
}
