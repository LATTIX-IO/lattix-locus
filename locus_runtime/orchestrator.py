from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from locus_runtime.persistence import load_state, mutate_state


@dataclass
class OrchestratorState:
    task: str
    current_step: int = 0
    plan: list[str] = field(default_factory=list)


@dataclass
class ApprovalRequest:
    id: str
    classification: str
    task: str
    status: str = "pending"


class ApprovalStore:
    def create(self, classification: str, task: str) -> ApprovalRequest:
        request = ApprovalRequest(id=str(uuid4()), classification=classification, task=task)

        def _mutate(snapshot: dict[str, Any]) -> None:
            approvals = list(snapshot.get("approvals", []))
            approvals.append(request.__dict__.copy())
            snapshot["approvals"] = approvals

        mutate_state(_mutate)
        return request

    def get(self, approval_id: str) -> ApprovalRequest | None:
        state = load_state()
        for item in state.get("approvals", []):
            if str(item.get("id")) == approval_id:
                return ApprovalRequest(**item)
        return None

    def decide(self, approval_id: str, decision: str) -> ApprovalRequest | None:
        updated: ApprovalRequest | None = None

        def _mutate(snapshot: dict[str, Any]) -> None:
            nonlocal updated
            approvals = list(snapshot.get("approvals", []))
            for item in approvals:
                if str(item.get("id")) == approval_id:
                    item["status"] = decision
                    updated = ApprovalRequest(**item)
                    break
            snapshot["approvals"] = approvals

        mutate_state(_mutate)
        return updated


_APPROVAL_STORE: ApprovalStore | None = None


def get_approval_store() -> ApprovalStore:
    global _APPROVAL_STORE
    if _APPROVAL_STORE is None:
        _APPROVAL_STORE = ApprovalStore()
    return _APPROVAL_STORE


def reset_approval_store() -> None:
    global _APPROVAL_STORE
    _APPROVAL_STORE = None


@dataclass
class WorkflowResult:
    final_output: str | None = None
    requires_approval: bool = False
    approval_status: str | None = None
    approval_request_id: str | None = None
    approved: bool = False


class Workflow:
    def __init__(self, workflow_id: str) -> None:
        self.workflow_id = workflow_id

    async def run(self, task: str, approval_request_id: str | None = None) -> WorkflowResult:
        if self.workflow_id == "security_compliance":
            store = get_approval_store()
            if not approval_request_id:
                request = store.create("confidential", task)
                return WorkflowResult(
                    final_output=None,
                    requires_approval=True,
                    approval_status="pending",
                    approval_request_id=request.id,
                    approved=False,
                )
            existing = store.get(approval_request_id)
            if existing and existing.status == "approved":
                return WorkflowResult(
                    final_output=f"Approved security compliance plan for {task}",
                    requires_approval=False,
                    approval_status="approved",
                    approval_request_id=approval_request_id,
                    approved=True,
                )
            return WorkflowResult(
                final_output=None,
                requires_approval=True,
                approval_status=existing.status if existing else "pending",
                approval_request_id=approval_request_id,
                approved=False,
            )
        return WorkflowResult(final_output=f"Review-ready output for {task}", approved=True)


def get_workflow_catalog() -> dict[str, Workflow]:
    return {
        "gtm_content": Workflow("gtm_content"),
        "security_compliance": Workflow("security_compliance"),
    }
