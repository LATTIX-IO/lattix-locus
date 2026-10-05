import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import NotFound from "@/app/not-found";

describe("NotFound", () => {
  it("renders page not found message", () => {
    render(<NotFound />);

    expect(screen.getByText("Page not found")).toBeTruthy();
    expect(screen.getByText(/does not exist/)).toBeTruthy();
  });

  it("has a link to home", () => {
    render(<NotFound />);

    const link = screen.getByText("Go to Home");
    expect(link).toBeTruthy();
    expect(link.getAttribute("href")).toBe("/home");
  });
});
