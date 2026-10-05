from locus_runtime.sandbox import (
    HostPlatform,
    IsolationStrategy,
    SandboxManager,
    SandboxPolicy,
    detect_host_platform,
)


def test_detect_host_platform_macos() -> None:
    assert detect_host_platform("Darwin") == HostPlatform.MACOS


def test_linux_policy_capabilities() -> None:
    policy = SandboxPolicy(platform=HostPlatform.LINUX)
    caps = policy.capabilities()
    assert caps.strict_syscalls is True
    assert caps.namespace_isolation is True
    assert caps.read_only_rootfs is True
    assert caps.network_namespace is True  # allow_network defaults to False


def test_sandbox_manager_forced_strategy() -> None:
    manager = SandboxManager(force_strategy=IsolationStrategy.HARDENED_DOCKER)
    assert manager.active_strategy == IsolationStrategy.HARDENED_DOCKER


def test_sandbox_manager_k8s_strategy() -> None:
    manager = SandboxManager(force_strategy=IsolationStrategy.K8S_GVISOR)
    assert manager.active_strategy == IsolationStrategy.K8S_GVISOR


def test_hardened_docker_has_seccomp_and_readonly() -> None:
    from locus_runtime.sandbox import ExecutionSpec

    manager = SandboxManager(force_strategy=IsolationStrategy.HARDENED_DOCKER)
    spec = ExecutionSpec(tool_id="test", command=["echo", "hello"])
    policy = SandboxPolicy(platform=HostPlatform.LINUX, allowed_executables=["echo"])
    plan = manager.plan(spec, policy)
    cmd = " ".join(plan.command)
    assert "--read-only" in cmd
    assert "--cap-drop=ALL" in cmd
    assert "--network=none" in cmd
    assert "--user=1000:1000" in cmd
    assert "--ipc=private" in cmd
    assert "--pids-limit=" in cmd
    assert "--memory=" in cmd
    assert "seccomp=" in cmd


def test_k8s_plan_returns_pod_spec_metadata() -> None:
    from locus_runtime.sandbox import ExecutionSpec

    manager = SandboxManager(force_strategy=IsolationStrategy.K8S_GVISOR)
    spec = ExecutionSpec(tool_id="test", command=["python", "-c", "1+1"])
    policy = SandboxPolicy(platform=HostPlatform.LINUX, allowed_executables=["python"])
    plan = manager.plan(spec, policy)
    assert plan.backend == "k8s-gvisor"
    assert "runtimeClassName" in plan.metadata
    assert plan.metadata["runtimeClassName"] == "locus-sandbox"
    assert plan.metadata["securityContext"]["runAsNonRoot"] is True


def test_restricted_process_fallback_is_disabled_by_default() -> None:
    from locus_runtime.sandbox import ExecutionSpec

    manager = SandboxManager(force_strategy=IsolationStrategy.RESTRICTED_PROCESS)
    spec = ExecutionSpec(tool_id="test", command=["echo", "hello"])
    policy = SandboxPolicy(platform=HostPlatform.LINUX, allowed_executables=["echo"])

    try:
        manager.plan(spec, policy)
        assert False, "expected restricted-process fallback to fail closed"
    except PermissionError as exc:
        assert "restricted-process fallback is disabled" in str(exc)
