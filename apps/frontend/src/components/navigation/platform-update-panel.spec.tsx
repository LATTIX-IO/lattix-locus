import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PlatformUpdatePanel, UpdatesPanel } from "@/components/navigation/platform-update-panel";

const {
  checkMock,
  statusMock,
  channelMock,
  installMock,
  listenMock,
} = vi.hoisted(() => ({
  checkMock: vi.fn(),
  statusMock: vi.fn(),
  channelMock: vi.fn(),
  installMock: vi.fn(),
  listenMock: vi.fn(),
}));

vi.mock("@/lib/desktop-shell", () => ({
  useIsDesktopShell: () => true,
  getTauriGlobal: () => ({
    app: { getVersion: () => Promise.resolve("0.4.0") },
    event: { listen: listenMock },
  }),
  checkDesktopUpdate: checkMock,
  getDesktopUpdateStatus: statusMock,
  setDesktopUpdateChannel: channelMock,
  installDesktopUpdate: installMock,
}));
vi.mock("@/lib/api", () => ({
  getSystemUpdateStatus: vi.fn().mockResolvedValue({ active_runs: 0 }),
}));

type State = "idle" | "up_to_date" | "available" | "error" | "checking";

function status(state: State, detail = "", version: string | null = null) {
  return {
    channel: "dev",
    state,
    current_version: "0.4.0",
    version,
    detail,
  };
}

beforeEach(() => {
  checkMock.mockReset();
  statusMock.mockReset();
  channelMock.mockReset();
  installMock.mockReset();
  listenMock.mockReset();
  listenMock.mockResolvedValue(() => {});
  statusMock.mockResolvedValue(status("idle"));
});

describe("Locus desktop update check", () => {
  it("does not start competing silent update checks when sidebar and Settings mount", async () => {
    render(
      <>
        <PlatformUpdatePanel />
        <UpdatesPanel />
      </>,
    );
    await waitFor(() => expect(statusMock).toHaveBeenCalledTimes(2));
    expect(checkMock).not.toHaveBeenCalled();
    expect(screen.getByText("Dev: Idle")).toBeInTheDocument();
  });

  it("reports the discovered version and exposes the manual install action", async () => {
    statusMock
      .mockResolvedValueOnce(status("idle"))
      .mockResolvedValueOnce(status("available", "dev channel reports an update; bytes are verified before installation.", "0.10.3"));
    checkMock.mockResolvedValueOnce("0.10.3");
    render(<UpdatesPanel />);
    await screen.findByText("Idle");
    fireEvent.click(screen.getByRole("button", { name: "Check now" }));
    await screen.findByText("v0.10.3 available");
    expect(screen.getByText("dev channel reports an update; bytes are verified before installation.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Update & Restart" })).toBeEnabled();
    expect(installMock).not.toHaveBeenCalled();
  });

  it("provides explicit up-to-date feedback instead of silently remaining Idle", async () => {
    statusMock.mockResolvedValueOnce(status("idle")).mockResolvedValueOnce(
      status("up_to_date", "dev channel checked; no newer version was announced."),
    );
    checkMock.mockResolvedValueOnce(null);
    render(<UpdatesPanel />);
    await screen.findByText("Idle");
    fireEvent.click(screen.getByRole("button", { name: "Check now" }));
    await screen.findByText("Up to date");
    expect(screen.getByText(/dev channel checked;/)).toBeInTheDocument();
  });

  it("does not misreport feed errors as up to date", async () => {
    statusMock.mockResolvedValueOnce(status("idle")).mockResolvedValueOnce(
      status("error", "dev channel update check failed: HTTP 403"),
    );
    checkMock.mockRejectedValueOnce(new Error("HTTP 403"));
    render(<UpdatesPanel />);
    await screen.findByText("Idle");
    fireEvent.click(screen.getByRole("button", { name: "Check now" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("alert")).toHaveTextContent("HTTP 403");
    expect(screen.queryByText("Up to date")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check now" })).toBeEnabled();
  });

  it("displays background feed failures without requiring another click", async () => {
    statusMock.mockResolvedValue(status("error", "dev signed update feed unavailable"));
    render(<UpdatesPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("dev signed update feed unavailable");
    expect(checkMock).not.toHaveBeenCalled();
  });

  it("recovers an initial status-read failure on a later valid native update event", async () => {
    statusMock.mockRejectedValueOnce(new Error("initial connection failed"));
    render(<UpdatesPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("initial connection failed");
    const handler = listenMock.mock.calls.find(([eventName]) => eventName === "update-status")?.[1];
    expect(typeof handler).toBe("function");
    act(() => handler({ payload: status("up_to_date", "dev channel checked; no newer version was announced.") }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(screen.getByRole("status")).toHaveTextContent("Up to date");
  });

  it("shows an explicit completed result from an older shell that still reports idle", async () => {
    checkMock.mockResolvedValueOnce(null);
    statusMock.mockResolvedValue(status("idle"));
    render(<UpdatesPanel />);
    await screen.findByText("Idle");
    fireEvent.click(screen.getByRole("button", { name: "Check now" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Up to date"));
    expect(checkMock).toHaveBeenCalledTimes(1);
  });

  it("disables repeated checks and exposes progress while an update check is pending", async () => {
    let finish: (version: string | null) => void = () => {};
    checkMock.mockImplementationOnce(
      () => new Promise<string | null>((resolve) => { finish = resolve; }),
    );
    statusMock.mockResolvedValueOnce(status("idle")).mockResolvedValueOnce(
      status("up_to_date", "dev channel checked; no newer version was announced."),
    );
    render(<UpdatesPanel />);
    await screen.findByText("Idle");
    fireEvent.click(screen.getByRole("button", { name: "Check now" }));
    expect(screen.getByRole("button", { name: "Checking…" })).toBeDisabled();
    expect(checkMock).toHaveBeenCalledTimes(1);
    await act(async () => { finish(null); });
    await screen.findByText("Up to date");
    expect(screen.getByRole("button", { name: "Check now" })).toBeEnabled();
  });
});
