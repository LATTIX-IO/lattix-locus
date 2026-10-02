from __future__ import annotations

from pydantic import BaseModel


class Settings(BaseModel):
    FEDERATION_ENABLED: bool = False
    FEDERATION_CLUSTER_NAME: str = ""
    FEDERATION_REGION: str = ""
    FEDERATION_PEER_ENDPOINTS: str = ""
    LOCUS_STATE_STORE: str = ".locus/runtime-state.json"
    A2A_JWT_SECRET: str = ""
