from __future__ import annotations

from dataclasses import dataclass

from locus_runtime.config import Settings


@dataclass(frozen=True)
class FederationStatus:
    enabled: bool
    cluster_name: str
    region: str
    peers: list[str]


class FederationTopologyService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def status(self) -> FederationStatus:
        peers = [
            item.strip()
            for item in self._settings.FEDERATION_PEER_ENDPOINTS.split(",")
            if item.strip()
        ]
        return FederationStatus(
            enabled=bool(self._settings.FEDERATION_ENABLED),
            cluster_name=str(self._settings.FEDERATION_CLUSTER_NAME),
            region=str(self._settings.FEDERATION_REGION),
            peers=peers,
        )
