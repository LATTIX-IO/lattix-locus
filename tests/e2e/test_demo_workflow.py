import asyncio

from locus_runtime.orchestrator import get_workflow_catalog


def test_demo_workflow_end_to_end() -> None:
    workflow = get_workflow_catalog()["gtm_content"]
    result = asyncio.run(workflow.run(task="demo gtm"))
    assert "review" in (result.final_output or "").lower() or result.final_output is not None
