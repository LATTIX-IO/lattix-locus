"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  deleteAgentDefinition,
  deleteGuardrailRuleset,
  deleteNodeDefinition,
  deleteWorkflowDefinition,
} from "@/lib/api";

type DeleteType = "workflow" | "agent" | "guardrail" | "node";

type Props = {
  itemType: DeleteType;
  itemId: string;
  itemName: string;
  onDeleted?: (id: string) => void;
  buttonClassName?: string;
};

export function TypedDeleteButton({ itemType, itemId, itemName, onDeleted, buttonClassName }: Props) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [typedName, setTypedName] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const canDelete = typedName.trim() === itemName;

  async function runDelete() {
    if (!canDelete) return;

    setDeleting(true);
    setErrorMessage(null);
    try {
      if (itemType === "workflow") {
        await deleteWorkflowDefinition(itemId);
      } else if (itemType === "agent") {
        await deleteAgentDefinition(itemId);
      } else if (itemType === "guardrail") {
        await deleteGuardrailRuleset(itemId);
      } else {
        await deleteNodeDefinition(itemId);
      }

      setOpen(false);
      setTypedName("");

      if (onDeleted) {
        onDeleted(itemId);
      } else {
        router.refresh();
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : `Unable to delete this ${itemType}.`);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className={buttonClassName ?? "fx-btn-warning px-2.5 py-1 text-xs font-medium"}
      >
        Delete
      </button>

      {open ? (
        <div className="fixed inset-0 z-[120] flex items-center justify-center bg-black/45 p-4">
          <div className="fx-panel w-full max-w-md p-4">
            <h3 className="text-base font-semibold">Confirm deletion</h3>
            <p className="fx-muted mt-1 text-sm">
              To delete this {itemType}, type its name exactly:
            </p>
            <p className="mt-1 text-sm font-semibold text-[var(--foreground)]">{itemName}</p>

            <input
              value={typedName}
              onChange={(event) => setTypedName(event.target.value)}
              className="fx-field mt-3 w-full px-3 py-2 text-sm"
              placeholder="Type exact name to confirm"
            />
            {errorMessage ? <p className="mt-2 text-xs text-[var(--fx-danger)]">{errorMessage}</p> : null}

            <div className="mt-3 flex justify-end gap-2">
              <button
                onClick={() => {
                  setOpen(false);
                  setTypedName("");
                  setErrorMessage(null);
                }}
                className="fx-btn-secondary px-3 py-2 text-sm"
              >
                Cancel
              </button>
              <button
                onClick={runDelete}
                disabled={!canDelete || deleting}
                className="fx-btn-warning px-3 py-2 text-sm disabled:opacity-50"
              >
                {deleting ? "Deleting..." : "Delete"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
