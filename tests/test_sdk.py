import base64

import httpx
import pytest

from artemis_provenance_sdk import Client, MarkedAsset, MarkingUnavailableError


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
