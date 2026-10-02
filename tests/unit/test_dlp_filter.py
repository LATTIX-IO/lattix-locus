import asyncio

from locus_runtime.envelope import Envelope
from locus_runtime.guardrails import DLPFilter, FilterContext


def test_dlp_filter_marks_pii() -> None:
    envelope = Envelope(
        source_agent="tester", action="execute", payload={"task": "email me at test@example.com"}
    )
    result = asyncio.run(DLPFilter().evaluate(envelope, FilterContext()))
    assert result.envelope.metadata["classification"] in {"internal", "confidential", "restricted"}
    assert result.envelope.payload["task"] == "email me at [REDACTED_EMAIL]"


def test_dlp_filter_redacts_multiple_secret_patterns() -> None:
    envelope = Envelope(
        source_agent="tester",
        action="execute",
        payload={
            "task": "call me at 555-222-1212 with card 4111 1111 1111 1111 and password=hunter2",
        },
    )

    result = asyncio.run(DLPFilter().evaluate(envelope, FilterContext()))

    assert result.action == "modify"
    assert "phone" in str(result.envelope.metadata.get("dlp_findings", ""))
    assert "credit_card" in str(result.envelope.metadata.get("dlp_findings", ""))
    assert "password" in str(result.envelope.metadata.get("dlp_findings", ""))
    assert "[REDACTED_PHONE]" in str(result.envelope.payload["task"])
    assert "[REDACTED_CREDIT_CARD]" in str(result.envelope.payload["task"])
    assert "[REDACTED_PASSWORD]" in str(result.envelope.payload["task"])


def test_dlp_filter_redacts_nested_payload_and_upgrades_restricted_findings() -> None:
    envelope = Envelope(
        source_agent="tester",
        action="execute",
        payload={
            "outer": {
                "notes": "SSN 123-45-6789 bearer abc.def.ghi",
                "secret": "api_key=sk-demo-secret-value",
            }
        },
    )

    result = asyncio.run(DLPFilter().evaluate(envelope, FilterContext()))

    assert result.action == "modify"
    assert result.envelope.metadata["classification"] == "restricted"
    assert "ssn" in str(result.envelope.metadata.get("dlp_findings", ""))
    assert "bearer_token" in str(result.envelope.metadata.get("dlp_findings", ""))
    assert "api_key" in str(result.envelope.metadata.get("dlp_findings", ""))
    assert result.envelope.payload["outer"]["notes"] == "SSN [REDACTED_SSN] bearer [REDACTED_TOKEN]"
    assert result.envelope.payload["outer"]["secret"] == "api_key=[REDACTED_API_KEY]"
