/**
 * Stand-in for the Locus desktop confirmation (`confirm_action` Tauri command, LOCUS-357).
 *
 * In Locus the shell shows a native dialog, signs the request with a secret the webview never
 * sees and sends it itself. Here we only model the contract the chat UI has to honour: the UI
 * must call this function and wait for it BEFORE the library resumes the interrupted run, and the
 * resume payload carries the shell's decision (and, in the real app, its proof).
 */

export type ConfirmRequest = {
  interruptId: string;
  action: string;
  summary: string;
  toolCallId?: string | undefined;
};

export type ConfirmResult = { approved: boolean; proof?: string | undefined };

type BenchWindow = Window & {
  __bakeoffConfirm?: (req: ConfirmRequest) => boolean | Promise<boolean>;
  __bench?: { confirmCalls: ConfirmRequest[] };
};

export async function confirmAction(req: ConfirmRequest): Promise<ConfirmResult> {
  const w = window as BenchWindow;
  w.__bench?.confirmCalls.push(req);
  // Headless runs install an automatic answer; a person gets the browser dialog as the stand-in
  // for the native one.
  const approved = w.__bakeoffConfirm ? await w.__bakeoffConfirm(req) : window.confirm(`${req.summary}\n\n(native confirmation stand-in)`);
  return approved ? { approved: true, proof: `stub-proof:${req.interruptId}` } : { approved: false };
}
