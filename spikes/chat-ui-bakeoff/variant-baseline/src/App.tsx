/** Control build: the same shell and Locus cards rendered statically, with no chat library. */
import {
  ApprovalCard,
  DECLARATIVE_SPEC,
  DeclarativeView,
  PlanPanel,
  RUN_SUMMARY_RESULT,
  RunErrorNotice,
  RunSummaryCard,
  Shell,
  ToolCallCard,
  parseRunSummary,
} from "@bakeoff/shared";

export function App() {
  const summary = parseRunSummary(RUN_SUMMARY_RESULT);
  return (
    <Shell
      variant="baseline (no library)"
      aside={<PlanPanel state={{ plan: [{ id: "s1", title: "Search the workspace", status: "done" }] }} />}
      chat={
        <div className="flex-1 overflow-y-auto p-4">
          <ToolCallCard name="search_files" argsText='{"query":"weekly report"}' status="complete" result="{}" />
          {summary ? <RunSummaryCard summary={summary} /> : null}
          <DeclarativeView spec={DECLARATIVE_SPEC} />
          <ApprovalCard request={{ interruptId: "i", message: "Send?", action: "send_email" }} onDecision={() => {}} />
          <RunErrorNotice message="example" />
        </div>
      }
    />
  );
}
