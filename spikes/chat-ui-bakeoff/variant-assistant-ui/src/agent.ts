// ADAPTER-GLUE (counted): the mock AG-UI event source as an @ag-ui/client agent.
// In Locus this class is replaced by an HttpAgent pointed at the gateway's AG-UI endpoint.
import { AbstractAgent, type BaseEvent, type RunAgentInput } from "@ag-ui/client";
import { Observable } from "rxjs";
import { mark, playScript, recordRunInput, scriptFor, type Scenario } from "@bakeoff/shared";

export class MockAgUiAgent extends AbstractAgent {
  constructor(private readonly scenario: Scenario, private readonly pace: number) {
    super({ threadId: "bakeoff-thread" });
  }

  run(input: RunAgentInput): Observable<BaseEvent> {
    recordRunInput(input);
    return new Observable<BaseEvent>((sub) => {
      mark(input.resume?.length ? "resume-start" : "run-start");
      const events = scriptFor(this.scenario, { threadId: input.threadId, runId: input.runId, resume: input.resume });
      return playScript(events, (e) => sub.next(e as unknown as BaseEvent), () => sub.complete(), this.pace);
    });
  }
}
