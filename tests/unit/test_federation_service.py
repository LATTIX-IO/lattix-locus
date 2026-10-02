from locus_runtime.config import Settings
from locus_runtime.federation import FederationTopologyService


def test_federation_service_parses_peers() -> None:
    settings = Settings(
        FEDERATION_ENABLED=True,
        FEDERATION_CLUSTER_NAME="cluster-a",
        FEDERATION_REGION="us-east",
        FEDERATION_PEER_ENDPOINTS="https://peer-a.example.com,https://peer-b.example.com",
    )
    status = FederationTopologyService(settings).status()
    assert status.enabled is True
    assert status.cluster_name == "cluster-a"
    assert len(status.peers) == 2
