import asyncio

from locus_runtime.orchestrator import get_approval_store, get_workflow_catalog


def test_confidential_workflow_requires_explicit_approval() -> None:
    workflow = get_workflow_catalog()["security_compliance"]
    first = asyncio.run(workflow.run(task="prepare iso review"))
    assert first.requires_approval is True
    assert first.approval_status == "pending"
    assert first.approval_request_id is not None

    get_approval_store().decide(first.approval_request_id, "approved")
    second = asyncio.run(
        workflow.run(
            task="prepare iso review",
            approval_request_id=first.approval_request_id,
        )
    )
    assert second.approved is True
    assert second.approval_status == "approved"
