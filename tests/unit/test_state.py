from locus_runtime.orchestrator import OrchestratorState


def test_state_defaults() -> None:
    state = OrchestratorState(task="demo")
    assert state.current_step == 0
    assert state.plan == []
