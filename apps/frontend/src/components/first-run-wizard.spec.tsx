import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getModelsOverview: vi.fn(),
  setProviderKey: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...api, DesktopConfirmationCancelledError: actual.DesktopConfirmationCancelledError };
});
vi.mock("@/components/settings/computer-use-section", () => ({
  BrowserTierControl: () => <div>browser tier control</div>,
}));
vi.mock("@/components/navigation/platform-update-panel", () => ({
  UpdatesPanel: () => <div>updates panel</div>,
}));
vi.mock("next/link", () => ({
  default: ({ children, href }: { children: ReactNode; href: string }) => <a href={href}>{children}</a>,
}));

import { FirstRunWizard, isFirstRunComplete, openFirstRunWizard } from "@/components/first-run-wizard";

function overview(ollamaAvailable: boolean) {
  return {
    providers: {
      openai: { configured: false, default_model: "" },
      nim: { configured: false, base_url: "", default_model: "", reference_example: "" },
      ollama: { available: ollamaAvailable, base_url: "", default_model: "", installed_models: ollamaAvailable ? [{ id: "llama3.2:3b", size_bytes: 1, modified_at: "" }] : [] },
    },
    external: [{ id: "nim", label: "NVIDIA NIM", configured: false, base_url: "", default_model: "", reference_example: "", key_required: true }],
    catalog: [],
  };
}

beforeEach(() => {
  api.getModelsOverview.mockReset().mockResolvedValue(overview(true));
  api.setProviderKey.mockReset().mockResolvedValue({ ok: true });
  window.localStorage.clear();
  delete (window as unknown as { __TAURI__?: unknown }).__TAURI__;
});

describe("FirstRunWizard", () => {
  it("opens by itself on the desktop app's first launch and walks every step", async () => {
    (window as unknown as { __TAURI__?: unknown }).__TAURI__ = { core: { invoke: vi.fn() } };
    render(<FirstRunWizard />);

    expect(await screen.findByRole("dialog", { name: /set up locus/i })).toBeInTheDocument();
    expect(screen.getByText(/step 1 of 5: welcome/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /get started/i }));
    expect(await screen.findByText(/ollama found/i)).toBeInTheDocument();
    expect(screen.getByText(/1 model ready/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^next$/i }));
    expect(screen.getByText("browser tier control")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^next$/i }));
    expect(screen.getByText("updates panel")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^next$/i }));
    expect(screen.getByText(/step 5 of 5: done/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /finish/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(isFirstRunComplete()).toBe(true);
  });

  it("can be skipped, and does not come back on its own", async () => {
    (window as unknown as { __TAURI__?: unknown }).__TAURI__ = { core: { invoke: vi.fn() } };
    const first = render(<FirstRunWizard />);
    fireEvent.click(await screen.findByRole("button", { name: /skip setup/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    first.unmount();

    render(<FirstRunWizard />);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("stays closed on the web profile until Settings re-opens it", async () => {
    render(<FirstRunWizard />);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    act(() => openFirstRunWizard());
    expect(await screen.findByRole("dialog", { name: /set up locus/i })).toBeInTheDocument();
  });

  it("stores an optional NIM key through the keys endpoint and says when Ollama is missing", async () => {
    api.getModelsOverview.mockResolvedValue(overview(false));
    render(<FirstRunWizard />);
    act(() => openFirstRunWizard());
    fireEvent.click(await screen.findByRole("button", { name: /get started/i }));

    expect(await screen.findByText(/ollama not found/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/nim api key/i), { target: { value: "nvapi-test" } });
    fireEvent.click(screen.getByRole("button", { name: /save key/i }));

    await waitFor(() => expect(api.setProviderKey).toHaveBeenCalledWith("nim", "nvapi-test"));
    expect(await screen.findByText(/stored in the os keychain/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/nim api key/i)).toHaveValue("");
  });
});
