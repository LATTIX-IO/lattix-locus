import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ConnectionNotice } from "@/components/connection-notice";

describe("ConnectionNotice", () => {
  it("renders nothing while the connection is live", () => {
    const { container } = render(<ConnectionNotice state="live" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("announces a reconnecting state politely", () => {
    render(<ConnectionNotice state="reconnecting" />);
    const notice = screen.getByRole("status");
    expect(notice).toHaveAttribute("aria-live", "polite");
    expect(notice).toHaveAttribute("data-connection-state", "reconnecting");
    expect(notice).toHaveTextContent("Connection lost — reconnecting");
    expect(notice).toHaveTextContent(/not finished until its status says so/i);
  });

  it("explains the polling fallback", () => {
    render(<ConnectionNotice state="polling" className="extra" />);
    const notice = screen.getByRole("status");
    expect(notice).toHaveTextContent("Live connection unavailable");
    expect(notice).toHaveClass("extra");
  });
});
