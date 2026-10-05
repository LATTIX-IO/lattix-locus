from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_helm_values_default_to_hosted_runtime_profile() -> None:
    values = _read("helm/lattix-locus/values.yaml")
    values_prod = _read("helm/lattix-locus/values-prod.yaml")

    assert "profile: hosted" in values
    assert "requireA2ARuntimeHeaders: true" in values
    assert "profile: hosted" in values_prod
    assert "requireA2ARuntimeHeaders: true" in values_prod


def test_helm_api_and_orchestrator_propagate_runtime_security_env() -> None:
    api_deployment = _read("helm/lattix-locus/templates/deployment-api.yaml")
    orchestrator_deployment = _read("helm/lattix-locus/templates/deployment-orchestrator.yaml")

    for template in (api_deployment, orchestrator_deployment):
        assert "name: LOCUS_RUNTIME_PROFILE" in template
        assert "name: LOCUS_REQUIRE_A2A_RUNTIME_HEADERS" in template
        assert "name: A2A_JWT_SECRET" in template
        assert "secretKeyRef:" in template


def test_helm_network_policy_targets_control_plane_workloads() -> None:
    network_policies = _read("helm/lattix-locus/templates/network-policies.yaml")

    assert "name: lattix-control-plane-default-deny" in network_policies
    assert "app: lattix-api" in network_policies
    assert "app: lattix-orchestrator" in network_policies
    assert "app: lattix-envoy" in network_policies


def test_helm_seccomp_and_runtimeclass_templates_share_labels_helper() -> None:
    helpers = _read("helm/lattix-locus/templates/_helpers.tpl")
    seccomp_template = _read("helm/lattix-locus/templates/seccomp-profile.yaml")
    runtimeclass_template = _read("helm/lattix-locus/templates/runtimeclass-sandbox.yaml")

    assert '{{- define "lattix-locus.labels" -}}' in helpers
    assert 'include "lattix-locus.labels" .' in seccomp_template
    assert 'include "lattix-locus.labels" .' in runtimeclass_template


def test_helm_image_templates_support_digest_pinning() -> None:
    helpers = _read("helm/lattix-locus/templates/_helpers.tpl")
    values = _read("helm/lattix-locus/values.yaml")

    assert '{{- define "lattix-locus.imageRef" -}}' in helpers
    assert "digest:" in values

    for relative_path in [
        "helm/lattix-locus/templates/deployment-api.yaml",
        "helm/lattix-locus/templates/deployment-orchestrator.yaml",
        "helm/lattix-locus/templates/deployment-opa.yaml",
        "helm/lattix-locus/templates/deployment-envoy.yaml",
        "helm/lattix-locus/templates/deployment-jaeger.yaml",
        "helm/lattix-locus/templates/deployment-vault.yaml",
        "helm/lattix-locus/templates/statefulset-postgres.yaml",
        "helm/lattix-locus/templates/statefulset-nats.yaml",
    ]:
        assert 'include "lattix-locus.imageRef"' in _read(relative_path)
