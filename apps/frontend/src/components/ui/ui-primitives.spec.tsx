import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

describe("design system primitives", () => {
  it("renders a button that defaults to type=button and merges classes", async () => {
    const onClick = vi.fn();
    render(
      <Button className="px-9" onClick={onClick}>
        Save
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Save" });
    expect(button).toHaveAttribute("type", "button");
    expect(button.className).toContain("px-9");
    expect(button.className).not.toContain("px-3.5");
    await userEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("renders the child element when asChild is set", () => {
    render(
      <Button asChild variant="secondary">
        <a href="/settings">Open settings</a>
      </Button>,
    );
    const link = screen.getByRole("link", { name: "Open settings" });
    expect(link).toHaveAttribute("href", "/settings");
    expect(link.className).toContain(buttonVariants({ variant: "secondary" }).split(" ")[0]);
  });

  it("toggles a labelled switch from the keyboard", async () => {
    function Harness() {
      const [checked, setChecked] = useState(false);
      return (
        <div>
          <Label htmlFor="capture">Capture content</Label>
          <Switch id="capture" checked={checked} onCheckedChange={setChecked} />
        </div>
      );
    }
    render(<Harness />);
    const toggle = screen.getByRole("switch", { name: "Capture content" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    toggle.focus();
    await userEvent.keyboard(" ");
    expect(toggle).toHaveAttribute("aria-checked", "true");
  });

  it("associates a label with an input", () => {
    render(
      <div>
        <Label htmlFor="name">Name</Label>
        <Input id="name" defaultValue="Locus" />
      </div>,
    );
    expect(screen.getByLabelText("Name")).toHaveValue("Locus");
  });

  it("switches tabs with the arrow keys", async () => {
    render(
      <Tabs defaultValue="a">
        <TabsList aria-label="Example">
          <TabsTrigger value="a">First</TabsTrigger>
          <TabsTrigger value="b">Second</TabsTrigger>
        </TabsList>
        <TabsContent value="a">Panel A</TabsContent>
        <TabsContent value="b">Panel B</TabsContent>
      </Tabs>,
    );
    screen.getByRole("tab", { name: "First" }).focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Second" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Panel B")).toBeInTheDocument();
  });

  it("opens a titled dialog and closes it with Escape", async () => {
    render(
      <Dialog>
        <DialogTrigger asChild>
          <Button>Open</Button>
        </DialogTrigger>
        <DialogContent>
          <DialogTitle>Confirm</DialogTitle>
          <DialogDescription>Details</DialogDescription>
        </DialogContent>
      </Dialog>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(screen.getByRole("dialog", { name: "Confirm" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders a badge with its text", () => {
    render(<Badge variant="success">Enforced</Badge>);
    expect(screen.getByText("Enforced")).toBeInTheDocument();
  });
});
