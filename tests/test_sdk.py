import base64
import json

import httpx
import pytest

from artemis_provenance_sdk import (
    TEXT_VERDICTS,
    Client,
    MarkedAsset,
    MarkedText,
    MarkingFailedError,
    MarkingUnavailableError,
)


class _Resp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def test_mark_image_parses_marked_asset(monkeypatch):
    marked_b64 = base64.b64encode(b"marked-bytes").decode()

    def fake_post(url, **kwargs):
        assert url.endswith("/mark")
        assert kwargs["headers"]["x-api-key"] == "k"
        assert kwargs["data"]["app_id"] == "avatar-studio"
        assert kwargs["data"]["entity_id"] == "default"
        return _Resp(
            200,
            {
                "eventId": "01ABC",
                "payloadId": 42,
                "sha256": "a" * 64,
                "phash": "deadbeef",
                "marks": {"c2pa": "applied", "watermark": "applied"},
                "mime": "image/png",
                "marked": marked_b64,
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    client = Client("http://dp.internal:8080", api_key="k")
    asset = client.mark_image(b"raw", app_id="avatar-studio")
    assert isinstance(asset, MarkedAsset)
    assert asset.event_id == "01ABC"
    assert asset.payload_id == 42
    assert asset.bytes == b"marked-bytes"
    assert asset.marks == {"c2pa": "applied", "watermark": "applied"}
    assert asset.mime == "image/png"


def test_mark_image_sends_context_title(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs["data"])
        return _Resp(
            200,
            {
                "eventId": "01ABC",
                "payloadId": 1,
                "sha256": "a" * 64,
                "marks": {},
                "mime": "image/png",
                "marked": base64.b64encode(b"m").decode(),
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    Client("http://dp.internal:8080", api_key="k").mark_image(
        b"raw", app_id="a", entity_id="acme", context={"title": "Sunset over Mars"}
    )
    assert captured["title"] == "Sunset over Mars"
    assert captured["entity_id"] == "acme"


def test_mark_image_raises_marking_unavailable_on_503(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _Resp(503, text="exhausted"))
    client = Client("http://dp.internal:8080", api_key="k")
    with pytest.raises(MarkingUnavailableError):
        client.mark_image(b"raw", app_id="a")


def test_mark_image_raises_on_connection_error(monkeypatch):
    def boom(url, **kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", boom)
    client = Client("http://dp.internal:8080", api_key="k")
    with pytest.raises(MarkingUnavailableError):
        client.mark_image(b"raw", app_id="a")


def test_verify_returns_result_dict(monkeypatch):
    def fake_post(url, **kwargs):
        assert url.endswith("/verify")
        assert kwargs["headers"]["x-api-key"] == "k"
        assert kwargs["data"] == {"content_type": "image"}
        return _Resp(
            200,
            {
                "result": "matched",
                "method": "watermark",
                "event": {"eventId": "01ABC"},
                "checks": {"watermark": {"present": True, "payloadId": "42"}},
                "local": True,
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    client = Client("http://dp.internal:8080/", api_key="k")
    result = client.verify(b"raw", content_type="image")
    assert result["result"] == "matched"
    assert result["event"]["eventId"] == "01ABC"
    assert result["local"] is True


def test_verify_raises_on_error_status(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _Resp(500, text="boom"))
    client = Client("http://dp.internal:8080", api_key="k")
    with pytest.raises(MarkingUnavailableError):
        client.verify(b"raw")


def test_mark_image_file_uses_basename(monkeypatch, tmp_path):
    captured = {}

    def fake_post(url, **kwargs):
        captured["filename"] = kwargs["files"]["file"][0]
        return _Resp(
            200,
            {
                "eventId": "01ABC",
                "payloadId": 1,
                "sha256": "a" * 64,
                "marks": {},
                "mime": "image/png",
                "marked": base64.b64encode(b"m").decode(),
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    p = tmp_path / "hero.png"
    p.write_bytes(b"raw")
    Client("http://dp.internal:8080", api_key="k").mark_image_file(p, app_id="a")
    assert captured["filename"] == "hero.png"


def _text_mark_payload(**overrides):
    payload = {
        "eventId": "01TXT",
        "payloadId": 99,
        "sha256": "b" * 64,
        "textCanonicalHash": "c" * 64,
        "canonicalization": "textcanon.v1",
        "contentType": "text",
        "mime": "text/plain; charset=utf-8",
        "marks": {"c2pa": "applied", "watermark": "not-applicable"},
        "softBinding": {"applied": False, "scheme": None},
        "manifestJws": "eyJhbGciOiJFUzI1NiJ9.payload.sig",
        "marked": base64.b64encode("Attested text.\n".encode()).decode(),
        "text": "Attested text.\n",
    }
    payload.update(overrides)
    return payload


def test_mark_text_record_only_defaults(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        assert url.endswith("/mark/text")
        assert kwargs["headers"]["x-api-key"] == "k"
        captured.update(kwargs["data"])
        return _Resp(200, _text_mark_payload())

    monkeypatch.setattr(httpx, "post", fake_post)
    client = Client("http://dp.internal:8080", api_key="k")
    attested = client.mark_text("Attested text.\n", app_id="newsroom")
    assert captured["app_id"] == "newsroom"
    assert captured["entity_id"] == "default"
    assert "soft_binding" not in captured
    assert "title" not in captured
    assert "context_json" not in captured
    assert isinstance(attested, MarkedText)
    assert attested.event_id == "01TXT"
    assert attested.payload_id == 99
    assert attested.text == "Attested text.\n"
    assert attested.bytes == "Attested text.\n".encode()
    assert attested.sha256 == "b" * 64
    assert attested.sha256_pre_embed is None
    assert attested.text_canonical_hash == "c" * 64
    assert attested.canonicalization == "textcanon.v1"
    assert attested.marks == {"c2pa": "applied", "watermark": "not-applicable"}
    assert attested.soft_binding == {"applied": False, "scheme": None}
    assert attested.manifest_jws == "eyJhbGciOiJFUzI1NiJ9.payload.sig"


def test_mark_text_soft_binding_and_context(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs["data"])
        return _Resp(
            200,
            _text_mark_payload(
                sha256PreEmbed="d" * 64,
                marks={"c2pa": "applied", "watermark": "applied"},
                softBinding={"applied": True, "scheme": "zwsb.v1"},
            ),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    client = Client("http://dp.internal:8080", api_key="k")
    attested = client.mark_text(
        "hello",
        app_id="newsroom",
        entity_id="acme",
        soft_binding=True,
        context={"title": "Note", "model": "some-model"},
    )
    assert captured["entity_id"] == "acme"
    assert captured["soft_binding"] == "true"
    assert captured["title"] == "Note"
    assert json.loads(captured["context_json"]) == {"model": "some-model"}
    assert attested.sha256_pre_embed == "d" * 64
    assert attested.soft_binding == {"applied": True, "scheme": "zwsb.v1"}
    assert attested.marks == {"c2pa": "applied", "watermark": "applied"}


def test_mark_text_soft_binding_false_is_sent(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs["data"])
        return _Resp(200, _text_mark_payload())

    monkeypatch.setattr(httpx, "post", fake_post)
    Client("http://dp.internal:8080", api_key="k").mark_text("t", app_id="a", soft_binding=False)
    assert captured["soft_binding"] == "false"


def test_mark_text_raises_marking_unavailable_on_503(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _Resp(503, text="payload ids exhausted"))
    with pytest.raises(MarkingUnavailableError):
        Client("http://dp.internal:8080", api_key="k").mark_text("t", app_id="a")


def test_mark_text_raises_on_connection_error(monkeypatch):
    def boom(url, **kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(MarkingUnavailableError):
        Client("http://dp.internal:8080", api_key="k").mark_text("t", app_id="a")


def test_mark_text_file_reads_utf8(monkeypatch, tmp_path):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs["data"])
        return _Resp(200, _text_mark_payload())

    monkeypatch.setattr(httpx, "post", fake_post)
    p = tmp_path / "article.md"
    p.write_text("Caf\u00e9 — attested\n", encoding="utf-8")
    Client("http://dp.internal:8080", api_key="k").mark_text_file(p, app_id="a")
    assert captured["text"] == "Caf\u00e9 — attested\n"


def test_verify_text_normalizes_keys_and_verdict(monkeypatch):
    def fake_post(url, **kwargs):
        assert url.endswith("/verify/text")
        assert kwargs["data"] == {"text": "some text"}
        assert kwargs["headers"]["x-api-key"] == "k"
        return _Resp(
            200,
            {
                "result": "matched",
                "verdict": "canonical-match",
                "method": "textcanon",
                "event": {"eventId": "01TXT"},
                "checks": {
                    "sha256": {"value": "b" * 64},
                    "textCanonical": {"value": "c" * 64, "algorithm": "textcanon.v1"},
                    "softBinding": {
                        "present": False,
                        "valid": False,
                        "payloadId": None,
                        "scheme": "zwsb.v1",
                    },
                },
                "generationWatermark": {
                    "status": "not-checked",
                    "reason": "provider detection APIs are access-gated",
                },
                "shortText": False,
                "notes": ["matches attested text up to formatting/invisible-character changes"],
                "local": True,
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    result = Client("http://dp.internal:8080", api_key="k").verify_text("some text")
    assert result["result"] == "matched"
    assert result["verdict"] == "canonical-match"
    assert result["verdict"] in TEXT_VERDICTS
    assert result["method"] == "textcanon"
    assert result["generation_watermark"]["status"] == "not-checked"
    assert "generationWatermark" not in result
    assert result["short_text"] is False
    assert "shortText" not in result
    assert result["local"] is True


def test_verify_text_raises_on_error_status(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _Resp(500, text="boom"))
    with pytest.raises(MarkingUnavailableError):
        Client("http://dp.internal:8080", api_key="k").verify_text("t")


# --- AI-generation declaration (never defaulted: the manifest is SIGNED) ---

AI_DST = "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"
HUMAN_DST = "http://cv.iptc.org/newscodes/digitalsourcetype/digitalCreation"

_TEXT_RESPONSE = {
    "eventId": "01TXT",
    "payloadId": 99,
    "sha256": "b" * 64,
    "textCanonicalHash": "c" * 64,
    "canonicalization": "textcanon.v1",
    "marks": {"c2pa": "applied", "watermark": "not-applicable"},
    "softBinding": {"applied": False, "scheme": None},
    "manifestJws": "h.p.s",
    "marked": base64.b64encode(b"t").decode(),
    "text": "t",
}


def _text_stub(monkeypatch, captured, **extra):
    def fake_post(url, **kwargs):
        captured.update(kwargs["data"])
        return _Resp(200, {**_TEXT_RESPONSE, **extra})

    monkeypatch.setattr(httpx, "post", fake_post)


def test_mark_text_sends_no_field_and_the_data_plane_defaults_it_to_ai(monkeypatch):
    """Omitting the argument keeps the field off the wire; absence IS the
    declaration, and the data plane resolves it to trainedAlgorithmicMedia (the
    Article 50 default)."""
    captured = {}
    _text_stub(monkeypatch, captured, aiGenerated=True, digitalSourceType=AI_DST)
    attested = Client("http://dp.internal:8080", api_key="k").mark_text("t", app_id="a")
    assert "ai_generated" not in captured
    assert attested.ai_generated is True
    assert attested.digital_source_type == AI_DST


def test_mark_text_forwards_an_explicit_declaration(monkeypatch):
    client = Client("http://dp.internal:8080", api_key="k")

    captured = {}
    _text_stub(monkeypatch, captured, aiGenerated=True, digitalSourceType=AI_DST)
    ai = client.mark_text("t", app_id="a", ai_generated=True)
    assert captured["ai_generated"] == "true"
    assert ai.ai_generated is True
    assert ai.digital_source_type == AI_DST

    captured = {}
    _text_stub(monkeypatch, captured, aiGenerated=False, digitalSourceType=HUMAN_DST)
    human = client.mark_text("t", app_id="a", ai_generated=False)
    assert captured["ai_generated"] == "false"
    assert human.ai_generated is False
    assert human.digital_source_type == HUMAN_DST


def test_mark_image_carries_the_same_declaration(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs["data"])
        return _Resp(
            200,
            {
                "eventId": "01ABC",
                "payloadId": 1,
                "sha256": "a" * 64,
                "marks": {"c2pa": "applied", "watermark": "applied"},
                "mime": "image/png",
                "aiGenerated": False,
                "digitalSourceType": HUMAN_DST,
                "marked": base64.b64encode(b"m").decode(),
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    asset = Client("http://dp.internal:8080", api_key="k").mark_image(
        b"raw", app_id="a", ai_generated=False
    )
    assert captured["ai_generated"] == "false"
    assert asset.ai_generated is False
    assert asset.digital_source_type == HUMAN_DST


def test_mark_image_echoes_none_when_the_response_omits_the_field(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs["data"])
        return _Resp(
            200,
            {
                "eventId": "01ABC",
                "payloadId": 1,
                "sha256": "a" * 64,
                "marks": {},
                "mime": "image/png",
                "marked": base64.b64encode(b"m").decode(),
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    asset = Client("http://dp.internal:8080", api_key="k").mark_image(b"raw", app_id="a")
    # No field on the wire (the data plane defaults it), and a response that
    # carries no echo — an older data plane — parses to None, never a guess.
    assert "ai_generated" not in captured
    assert asset.ai_generated is None
    assert asset.digital_source_type is None


# --- fail-closed marking ---------------------------------------------------
#
# An unreachable data plane and a failed mark are the same compliance outcome —
# nothing was marked — so they reach the caller the same way: raised, never
# returned. Unmarked output is only ever obtainable through an explicit except.

_REJECTED = {
    "code": "marking_failed",
    "contentType": "image",
    "requiredMark": "watermark",
    "reason": "ValueError: image too small to carry the reference watermark",
    "onMarkingFailure": "reject",
    "eventId": "01FAIL",
    "payloadId": 12,
    "sha256": "e" * 64,
    "marks": {"c2pa": "applied", "watermark": "failed"},
    "detail": "required mark (watermark) could not be produced for image",
    "marked": None,
}


def test_mark_image_raises_marking_failed_on_422(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _Resp(422, _REJECTED))
    client = Client("http://dp.internal:8080", api_key="k")
    with pytest.raises(MarkingFailedError) as excinfo:
        client.mark_image(b"raw", app_id="a")
    err = excinfo.value
    assert err.required_mark == "watermark"
    assert err.event_id == "01FAIL"
    assert err.on_marking_failure == "reject"
    assert "image too small" in (err.reason or "")
    # Nothing to ship: the data plane refused, so no asset exists.
    assert err.unmarked is None
    # Same contract as an unreachable data plane, so existing fail-open /
    # fail-closed handling applies unchanged.
    assert isinstance(err, MarkingUnavailableError)


def test_mark_image_raises_instead_of_returning_unmarked_bytes(monkeypatch):
    payload = {
        "eventId": "01OPEN",
        "payloadId": 13,
        "sha256": "f" * 64,
        "marks": {"c2pa": "applied", "watermark": "failed"},
        "mime": "image/png",
        "marked": base64.b64encode(b"UNMARKED").decode(),
        "markingFailed": True,
        "markingFailure": {
            **_REJECTED,
            "eventId": "01OPEN",
            "onMarkingFailure": "return_unmarked",
            "warning": "REQUIRED MARK MISSING (watermark). These bytes are NOT marked",
        },
    }
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _Resp(200, payload))
    client = Client("http://dp.internal:8080", api_key="k")
    with pytest.raises(MarkingFailedError) as excinfo:
        client.mark_image(b"raw", app_id="a")
    err = excinfo.value
    assert err.on_marking_failure == "return_unmarked"
    assert "NOT marked" in err.detail["warning"]
    # The unmarked output is reachable ONLY here — shipping it is a choice.
    assert isinstance(err.unmarked, MarkedAsset)
    assert err.unmarked.bytes == b"UNMARKED"
    assert err.unmarked.event_id == "01OPEN"


def test_mark_text_raises_marking_failed_when_the_manifest_is_missing(monkeypatch):
    body = {
        "code": "marking_failed",
        "contentType": "text",
        "requiredMark": "signed-manifest",
        "reason": "RuntimeError: KMS unavailable",
        "onMarkingFailure": "reject",
        "eventId": "01TXTFAIL",
        "payloadId": 42,
        "sha256": "b" * 64,
        "marks": {"c2pa": "failed", "watermark": "not-applicable"},
    }
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _Resp(422, body))
    client = Client("http://dp.internal:8080", api_key="k")
    with pytest.raises(MarkingFailedError) as excinfo:
        client.mark_text("t", app_id="a")
    assert excinfo.value.required_mark == "signed-manifest"
    assert excinfo.value.unmarked is None


def test_mark_text_hands_back_unattested_text_through_the_error(monkeypatch):
    payload = {
        "eventId": "01TXTOPEN",
        "payloadId": 43,
        "sha256": "b" * 64,
        "textCanonicalHash": "c" * 64,
        "canonicalization": "textcanon.v1",
        "contentType": "text",
        "mime": "text/plain; charset=utf-8",
        "marks": {"c2pa": "failed", "watermark": "not-applicable"},
        "softBinding": {"applied": False, "scheme": None},
        "manifestJws": None,
        "marked": base64.b64encode("unattested".encode()).decode(),
        "text": "unattested",
        "markingFailed": True,
        "markingFailure": {
            "code": "marking_failed",
            "contentType": "text",
            "requiredMark": "signed-manifest",
            "reason": "RuntimeError: KMS unavailable",
            "onMarkingFailure": "return_unmarked",
            "eventId": "01TXTOPEN",
            "payloadId": 43,
            "sha256": "b" * 64,
            "marks": {"c2pa": "failed", "watermark": "not-applicable"},
        },
    }
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _Resp(200, payload))
    client = Client("http://dp.internal:8080", api_key="k")
    with pytest.raises(MarkingFailedError) as excinfo:
        client.mark_text("unattested", app_id="a")
    err = excinfo.value
    assert isinstance(err.unmarked, MarkedText)
    assert err.unmarked.text == "unattested"
    assert err.unmarked.manifest_jws is None


def test_request_validation_422_is_not_a_marking_failure(monkeypatch):
    body = {"detail": [{"loc": ["body", "app_id"], "msg": "field required"}]}
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _Resp(422, body))
    client = Client("http://dp.internal:8080", api_key="k")
    with pytest.raises(MarkingUnavailableError) as excinfo:
        client.mark_image(b"raw", app_id="a")
    assert not isinstance(excinfo.value, MarkingFailedError)
