import { describe, expect, it, vi } from "vitest";

const redirectMock = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({
  redirect: redirectMock,
}));

import RootPage from "@/app/page";

describe("RootPage", () => {
  it("lands on Home (the shell sends a web visitor without a session to /auth)", () => {
    RootPage();

    expect(redirectMock).toHaveBeenCalledWith("/home");
  });
});
