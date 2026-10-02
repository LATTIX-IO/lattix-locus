import asyncio

from locus_runtime.orchestrator import get_workflow_catalog


def test_gtm_workflow_runs() -> None:
    workflow = get_workflow_catalog()["gtm_content"]
    result = asyncio.run(workflow.run(task="launch new product"))
    assert result.final_output is not None
