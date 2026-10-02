"""Lattix xFrontier agentic coding harness.

A self-contained, model-agnostic SWE agent scaffold designed to extract
high-quality long-horizon coding behaviour from local open-weight models
(gpt-oss, Qwen3-Coder, Devstral, ...) and to be benchmarked against
DeepSWE / SWE-bench-class evaluations.

Design lineage: mini-SWE-agent (minimal, replayable, linear trajectory) +
R2E-Gym/DeepSWE (fixed tool set, execution-graded, submit-or-zero) +
Aider (edit-format discipline with weak-model fallback).

All modules here are new and standalone — they do not import the FastAPI
monolith (``apps/backend/app/main.py``) so the harness can run headless in
CI and on remote benchmark runners.
"""

from __future__ import annotations

from frontier_runtime.harness.collaboration import (
    CollaborationResult,
    CollaborativeTeam,
    Contribution,
    Conversation,
    build_collaborative_team,
)
from frontier_runtime.harness.development import (
    ChatTurn,
    DevelopmentResult,
    DevelopmentWorkflow,
    build_development_workflow,
)
from frontier_runtime.harness.executor import (
    DockerContainerExecutor,
    ExecResult,
    Executor,
    LocalDirectExecutor,
    LocalSandboxExecutor,
)
from frontier_runtime.harness.integrations import (
    DeliveryPolicy,
    DevFlow,
    DevFlowResult,
    FileSpecSource,
    GhCliGitHub,
    GitHubDelivery,
    InlineSpecSource,
    LinearSpecSource,
    Spec,
    SpecSource,
)
from frontier_runtime.harness.loop import AgentLoop, LoopBudgets, LoopOutcome, LoopResult
from frontier_runtime.harness.model_profiles import ModelCapabilityProfile, resolve_profile
from frontier_runtime.harness.swe_agent import SweAgent, SweAgentResult, SweTask
from frontier_runtime.harness.team import (
    ModeratorVerdict,
    Review,
    ReviewAgent,
    TeamFlow,
    TeamResult,
    build_team_from_shipped,
)
from frontier_runtime.harness.tools import CodingToolset
from frontier_runtime.harness.trajectory import TrajectoryRecorder
from frontier_runtime.harness.workspace import Workspace
from frontier_runtime.harness.workspace_binding import (
    ProvisionedWorkspace,
    WorkspaceBinding,
    WorkspaceManager,
)

__all__ = [
    "AgentLoop",
    "ChatTurn",
    "CodingToolset",
    "CollaborationResult",
    "CollaborativeTeam",
    "Contribution",
    "Conversation",
    "build_collaborative_team",
    "DeliveryPolicy",
    "DevFlow",
    "DevFlowResult",
    "DevelopmentResult",
    "DevelopmentWorkflow",
    "build_development_workflow",
    "DockerContainerExecutor",
    "FileSpecSource",
    "GhCliGitHub",
    "GitHubDelivery",
    "InlineSpecSource",
    "LinearSpecSource",
    "Spec",
    "SpecSource",
    "ExecResult",
    "Executor",
    "LocalDirectExecutor",
    "LocalSandboxExecutor",
    "LoopBudgets",
    "LoopOutcome",
    "LoopResult",
    "ModelCapabilityProfile",
    "ProvisionedWorkspace",
    "WorkspaceBinding",
    "WorkspaceManager",
    "ModeratorVerdict",
    "Review",
    "ReviewAgent",
    "TeamFlow",
    "TeamResult",
    "build_team_from_shipped",
    "SweAgent",
    "SweAgentResult",
    "SweTask",
    "TrajectoryRecorder",
    "Workspace",
    "resolve_profile",
]
