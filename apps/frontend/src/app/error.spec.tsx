import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import GlobalError from "@/app/error";

describe("GlobalError", () => {
  it("renders the error message", () => {
    const error = new Error("Test failure");
    render(<GlobalError error={error} reset={vi.fn()} />);

    expect(screen.getByText("Something went wrong")).toBeTruthy();
    expect(screen.getByText("Test failure")).toBeTruthy();
  });

  it("shows error digest when available", () => {
    const error = Object.assign(new Error("Oops"), { digest: "abc123" });
    render(<GlobalError error={error} reset={vi.fn()} />);

    expect(screen.getByText(/abc123/)).toBeTruthy();
  });

  it("calls reset when retry button is clicked", () => {
    const reset = vi.fn();
    render(<GlobalError error={new Error("fail")} reset={reset} />);

    fireEvent.click(screen.getByText("Try again"));
    expect(reset).toHaveBeenCalledTimes(1);
  });

  it("has a link to home", () => {
    render(<GlobalError error={new Error("fail")} reset={vi.fn()} />);

    const link = screen.getByText("Go to Home");
    expect(link).toBeTruthy();
    expect(link.getAttribute("href")).toBe("/home");
  });
});
