from locus_runtime.envelope import Envelope, EnvelopeStatus, envelope_from_json, envelope_to_json


def test_envelope_round_trip() -> None:
    envelope = Envelope(source_agent="tester", action="execute", payload={"task": "demo"})
    restored = envelope_from_json(envelope_to_json(envelope))
    assert restored.id == envelope.id
    assert restored.status == EnvelopeStatus.PENDING
