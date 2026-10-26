import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getUserSettings: vi.fn(),
  getModelsOverview: vi.fn(),
  getMcpServers: vi.fn(),
  getSkills: vi.fn(),
  getWorkspaceFolders: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

import { ComposerControls } from "@/components/composer-controls";

const installed = [
  { id: "gpt-oss:20b-ctx32k" },
  { id: "nomic-embed-text:latest" },
];

function setOverview(models = installed) {
  api.getModelsOverview.mockResolvedValue({
    providers: { ollama: { available: true, installed_models: models } },
    external: [],
  });
}

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset());
  api.getUserSettings.mockResolvedValue({
    preferred_model: "",
    preferred_reasoning_effort: "",
    default_mode: "execute",
    default_working_folder: "",
  });
  setOverview();
  api.getMcpServers.mockResolvedValue([]);
  api.getSkills.mockResolvedValue([]);
  api.getWorkspaceFolders.mockResolvedValue({ folders: [] });
});

describe("ComposerControls model readiness", () => {

  it("uses the explicit orchestrator default, not the stale preferred-model alias", async () => {
    api.getUserSettings.mockResolvedValue({
      preferred_model: "ollama/uninstalled:latest",
      orchestrator_model: "ollama/gpt-oss:20b-ctx32k",
      worker_routing: "auto",
      default_mode: "execute",
      preferred_reasoning_effort: "",
      default_working_folder: "",
    });
    const readiness = vi.fn();
    const change = vi.fn();
    render(<ComposerControls onChange={change} onModelReadinessChange={readiness} />);
    await waitFor(() => expect(readiness).toHaveBeenLastCalledWith(true, ""));
    expect(change).toHaveBeenLastCalledWith(expect.objectContaining({
      model: "ollama/gpt-oss:20b-ctx32k",
    }));
  });

  it("never substitutes a different orchestration model for explicit Auto before admission is available", async () => {
    api.getUserSettings.mockResolvedValue({
      preferred_model: "ollama/gpt-oss:20b-ctx32k",
      orchestrator_model: "auto",
      worker_routing: "auto",
      default_mode: "execute",
      preferred_reasoning_effort: "",
      default_working_folder: "",
    });
    const readiness = vi.fn();
    const change = vi.fn();
    render(<ComposerControls onChange={change} onModelReadinessChange={readiness} />);
    await waitFor(() => expect(readiness).toHaveBeenLastCalledWith(
      false, expect.stringContaining('Saved default model "auto" is unavailable'),
    ));
    expect(change).toHaveBeenLastCalledWith(expect.objectContaining({ model: undefined }));
  });

  it("keeps an unavailable saved model blocked until the user selects an installed model", async () => {
    api.getUserSettings.mockResolvedValue({
      preferred_model: "ollama/llama3.2:3b",
      default_mode: "execute",
      preferred_reasoning_effort: "",
      default_working_folder: "",
    });
    // A configured generic Ollama provider must not reintroduce its stale default.
    api.getModelsOverview.mockResolvedValue({
      providers: { ollama: { available: true, installed_models: installed } },
      external: [{ id: "ollama", label: "Ollama", configured: true, default_model: "llama3.2:3b" }],
    });
    const onReadiness = vi.fn();
    const onChange = vi.fn();
    render(<ComposerControls onChange={onChange} onModelReadinessChange={onReadiness} />);

    await waitFor(() => expect(onReadiness).toHaveBeenLastCalledWith(
      false, expect.stringContaining('Saved default model "ollama/llama3.2:3b" is unavailable'),
    ));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ model: undefined }));

    fireEvent.click(screen.getByRole("button", { name: "Model" }));
    expect(screen.queryByRole("button", { name: /nomic-embed-text/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /llama3.2:3b/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /gpt-oss:20b-ctx32k/i }));

    await waitFor(() => expect(onReadiness).toHaveBeenLastCalledWith(true, ""));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({
      model: "ollama/gpt-oss:20b-ctx32k",
    }));
  });

  it("keeps submission blocked throughout asynchronous discovery", async () => {
    let resolveOverview!: (overview: unknown) => void;
    api.getModelsOverview.mockReturnValue(
      new Promise((resolve) => { resolveOverview = resolve; }),
    );
    const onReadiness = vi.fn();
    render(<ComposerControls onChange={vi.fn()} onModelReadinessChange={onReadiness} />);
    expect(onReadiness).toHaveBeenLastCalledWith(false, expect.stringContaining("Checking available models"));

    resolveOverview({
      providers: { ollama: { available: true, installed_models: installed } },
      external: [],
    });
    await waitFor(() => expect(onReadiness).toHaveBeenLastCalledWith(true, ""));
  });

  it("fails closed if model discovery cannot be completed", async () => {
    api.getModelsOverview.mockRejectedValue(new Error("backend not reachable"));
    const onReadiness = vi.fn();
    render(<ComposerControls onChange={vi.fn()} onModelReadinessChange={onReadiness} />);
    await waitFor(() => expect(onReadiness).toHaveBeenLastCalledWith(
      false, expect.stringContaining("Could not check models"),
    ));
  });

  it("excludes embedding-only models and refuses an empty chat inventory", async () => {
    setOverview([{ id: "nomic-embed-text:latest" }]);
    const onReadiness = vi.fn();
    render(<ComposerControls onChange={vi.fn()} onModelReadinessChange={onReadiness} />);
    await waitFor(() => expect(onReadiness).toHaveBeenLastCalledWith(
      false, expect.stringContaining("No chat-capable models"),
    ));
    fireEvent.click(screen.getByRole("button", { name: "Model" }));
    expect(screen.getByText("No models configured")).toBeInTheDocument();
  });

  it("honors a configured hosted model when no Ollama models are available", async () => {
    api.getModelsOverview.mockResolvedValue({
      providers: { ollama: { available: false, installed_models: [] } },
      external: [{ id: "openai", label: "OpenAI", configured: true, default_model: "gpt-test" }],
    });
    const onReadiness = vi.fn();
    const onChange = vi.fn();
    render(<ComposerControls onChange={onChange} onModelReadinessChange={onReadiness} />);
    await waitFor(() => expect(onReadiness).toHaveBeenLastCalledWith(true, ""));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ model: "gpt-test" }));
  });
});
