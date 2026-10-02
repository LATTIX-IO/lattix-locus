"use client";

import { useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { archiveWorkflowRun } from "@/lib/api";

type Props = {
  runId: string;
  buttonClassName?: string;
  label?: string;
  ariaLabel?: string;
  iconOnly?: boolean;
};

export function RunArchiveButton({ runId, buttonClassName, label, ariaLabel, iconOnly = false }: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [busy, setBusy] = useState(false);

  async function archiveRun() {
    setBusy(true);
    try {
      await archiveWorkflowRun(runId);
      const activeSessionId = searchParams?.get("session");
      if (pathname === "/inbox" && activeSessionId === runId) {
        router.replace("/inbox");
      }
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      onClick={archiveRun}
      disabled={busy}
      aria-label={ariaLabel ?? (busy ? "Archiving run" : (label ?? "Archive run"))}
      className={buttonClassName ?? "fx-btn-secondary px-2.5 py-1 text-xs font-medium disabled:opacity-60"}
    >
      {iconOnly ? (
        <svg viewBox="0 0 16 16" className="mx-auto h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
          <path d="M3.5 4.5h9" strokeLinecap="round" />
          <path d="M6 2.75h4" strokeLinecap="round" />
          <path d="M5 4.5v7.25c0 .41.34.75.75.75h4.5c.41 0 .75-.34.75-.75V4.5" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M6.75 6.5v4" strokeLinecap="round" />
          <path d="M9.25 6.5v4" strokeLinecap="round" />
        </svg>
      ) : busy ? "Archiving..." : (label ?? "Archive")}
    </button>
  );
}
