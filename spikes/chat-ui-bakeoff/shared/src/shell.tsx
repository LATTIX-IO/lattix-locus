import { useState, type ReactNode } from "react";
import { Button } from "./ui";

/** Same page chrome for both variants: header, theme toggle, chat region, state aside. */
export function Shell({ variant, chat, aside, onTheme }: { variant: string; chat: ReactNode; aside: ReactNode; onTheme?: (dark: boolean) => void }) {
  const [dark, setDark] = useState(false);
  const toggle = () => {
    const next = !dark;
    setDark(next);
    const html = document.documentElement;
    html.classList.toggle("theme-dark", next);
    html.classList.toggle("theme-light", !next);
    onTheme?.(next);
  };
  return (
    <div className="flex h-screen flex-col bg-background text-foreground">
      <header className="flex items-center justify-between border-b border-border px-4 py-2">
        <h1 className="text-sm font-semibold">Locus chat bake-off: {variant}</h1>
        <nav aria-label="Scenarios" className="flex items-center gap-2 text-sm">
          <a className="underline focus-visible:outline-2 focus-visible:outline-ring" href="?scenario=run">Run</a>
          <a className="underline focus-visible:outline-2 focus-visible:outline-ring" href="?scenario=long">Long thread</a>
          <Button variant="outline" aria-pressed={dark} onClick={toggle}>
            {dark ? "Light theme" : "Dark theme"}
          </Button>
        </nav>
      </header>
      <div className="flex min-h-0 flex-1">
        <main className="flex min-h-0 min-w-0 flex-1 flex-col" aria-label="Chat">{chat}</main>
        <aside className="hidden w-72 shrink-0 border-l border-border p-3 md:block" aria-label="Run state">{aside}</aside>
      </div>
    </div>
  );
}
