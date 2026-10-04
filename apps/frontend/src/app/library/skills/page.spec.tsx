import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getSkills: vi.fn(),
  saveSkill: vi.fn(),
  deleteSkill: vi.fn(),
  importSkill: vi.fn(),
  scanSkill: vi.fn(),
  getUserSkills: vi.fn(),
  saveUserSkills: vi.fn(),
}));
const searchState = vi.hoisted(() => ({ current: new URLSearchParams() }));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...api, DesktopConfirmationCancelledError: actual.DesktopConfirmationCancelledError, SecurityChangeConfirmationRequired: actual.SecurityChangeConfirmationRequired };
});
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => searchState.current,
}));
vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children: ReactNode; href: string }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

import SkillsInventoryPage from "@/app/library/skills/page";

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset());
  searchState.current = new URLSearchParams();
  api.getSkills.mockResolvedValue([]);
  api.getUserSkills.mockResolvedValue({ principal_id: "me", skills: ["/research-brief"] });
  api.saveUserSkills.mockResolvedValue({ principal_id: "me", skills: ["/research-brief", "/incident-triage"] });
});

describe("Library → Skills → Personal", () => {
  it("shows personal skills in their own tab and saves them", async () => {
    render(<SkillsInventoryPage />);

    fireEvent.mouseDown(screen.getByRole("tab", { name: "Personal" }));
    const field = await screen.findByLabelText("Skills");
    expect(field).toHaveValue("/research-brief");
    fireEvent.change(field, { target: { value: "/research-brief\n/incident-triage" } });
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(api.saveUserSkills).toHaveBeenCalledWith({ skills: ["/research-brief", "/incident-triage"] }));
  });

  it("opens the Personal tab from ?tab=personal", async () => {
    searchState.current = new URLSearchParams("tab=personal");
    render(<SkillsInventoryPage />);

    expect(screen.getByRole("tab", { name: "Personal" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("group", { name: /personal skills/i })).toBeInTheDocument();
  });
});
