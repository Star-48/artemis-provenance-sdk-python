"""Thin client for the Artemis Provenance data-plane marking API.

The SDK never marks locally — it calls the data-plane container the customer
runs in their own network, so content never leaves the VPC. Fail modes are
explicit: :class:`MarkingUnavailableError` is catchable so customer pipelines
choose fail-open vs fail-closed CONSCIOUSLY (spec §9).
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, TypedDict, Union

import httpx

__all__ = ["Client", "MarkedAsset", "MarkingUnavailableError", "VerifyResult"]


class MarkingUnavailableError(Exception):
    """The data plane could not be reached or could not mark the asset.

    Catch this to decide fail-open (ship unmarked — a compliance gap) vs
    fail-closed (block the asset). The choice is the customer's to make;
    document the implications either way.
    """


@dataclass
class MarkedAsset:
    """A successfully marked asset returned by :meth:`Client.mark_image`.

    Attributes:
        bytes: The marked output bytes — this is what you ship to end users.
        event_id: Provenance event id recorded on the control plane.
        payload_id: Numeric watermark payload id embedded in the asset.
        sha256: SHA-256 of the marked bytes (hex).
        marks: Which marks were applied, e.g. ``{"c2pa": "applied", "watermark": "applied"}``.
        mime: MIME type of the marked bytes.
    """

    bytes: bytes
    event_id: str
    payload_id: int
    sha256: str
    marks: Dict[str, str]
    mime: str


class VerifyResult(TypedDict, total=False):
    """Shape of the dict returned by :meth:`Client.verify`.

    Keys:
        result: ``"matched"`` or ``"no-match"``.
        method: How the match was made (e.g. watermark, c2pa, sha256) or ``None``.
        event: The matched provenance event, or ``None``.
        checks: Per-signal detail (``watermark``, ``c2pa``, ``sha256``).
        local: ``True`` — verification ran inside your network.
    """

    result: str
    method: Optional[str]
    event: Optional[Dict[str, Any]]
    checks: Dict[str, Any]
    local: bool


class Client:
    """Client for a customer-deployed Provenance data plane.

    Args:
        endpoint: Base URL of the data-plane container,
            e.g. ``"http://provenance-dp.internal:8080"``.
        api_key: The data plane's local API key (sent as ``x-api-key``).
        timeout: Per-request timeout in seconds (default 30).
    """

    def __init__(self, endpoint: str, api_key: str, timeout: float = 30.0) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def mark_image(
        self,
        image_bytes: bytes,
        *,
        app_id: str,
        entity_id: str = "default",
        context: Optional[Dict[str, str]] = None,
        filename: str = "asset",
    ) -> MarkedAsset:
        """Mark an image: C2PA manifest + invisible watermark + registry event.

        Call this at the end of your generation pipeline and ship the returned
        :attr:`MarkedAsset.bytes` instead of the original.

        Args:
            image_bytes: The raw image to mark.
            app_id: Your application id (groups events per app on the console).
            entity_id: Entity the asset belongs to (per-entity provenance chain).
            context: Optional metadata; ``context["title"]`` becomes the C2PA title.
            filename: Filename hint for content-type sniffing.

        Raises:
            MarkingUnavailableError: The data plane is unreachable or refused
                to mark. Decide fail-open vs fail-closed consciously.
        """
        data = {
            "app_id": app_id,
            "entity_id": entity_id,
            "title": (context or {}).get("title", filename),
        }
        files = {"file": (filename, image_bytes, "application/octet-stream")}
        try:
            resp = httpx.post(
                f"{self._endpoint}/mark",
                data=data,
                files=files,
                headers={"x-api-key": self._api_key},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise MarkingUnavailableError(f"data plane unreachable: {exc}") from exc

        if resp.status_code == 503:
            raise MarkingUnavailableError(f"marking unavailable: {resp.text}")
        if resp.status_code != 200:
            raise MarkingUnavailableError(f"mark failed {resp.status_code}: {resp.text}")

        d = resp.json()
        return MarkedAsset(
            bytes=base64.b64decode(d["marked"]),
            event_id=d["eventId"],
            payload_id=int(d["payloadId"]),
            sha256=d["sha256"],
            marks=d["marks"],
            mime=d["mime"],
        )

    def mark_image_file(
        self,
        path: Union[str, "os.PathLike[str]"],
        *,
        app_id: str,
        entity_id: str = "default",
        context: Optional[Dict[str, str]] = None,
    ) -> MarkedAsset:
        """Convenience wrapper: read ``path`` and :meth:`mark_image` its bytes."""
        with open(path, "rb") as f:
            return self.mark_image(
                f.read(),
                app_id=app_id,
                entity_id=entity_id,
                context=context,
                filename=os.path.basename(os.fspath(path)),
            )

    def verify(
        self,
        media_bytes: bytes,
        *,
        content_type: Optional[str] = None,
        filename: str = "asset",
    ) -> VerifyResult:
        """Verify LOCALLY via the data plane.

        The content never leaves your network; only the extracted id is
        resolved against the control plane.

        Args:
            media_bytes: The media to verify (image, video, or audio bytes).
            content_type: Optional hint: ``"image"``, ``"video"`` or ``"audio"``.
            filename: Filename hint for content-type sniffing.

        Returns:
            The provenance result — see :class:`VerifyResult`.

        Raises:
            MarkingUnavailableError: The data plane is unreachable or the
                verify call failed.
        """
        data = {"content_type": content_type} if content_type else {}
        files = {"file": (filename, media_bytes, "application/octet-stream")}
        try:
            resp = httpx.post(
                f"{self._endpoint}/verify",
                data=data,
                files=files,
                headers={"x-api-key": self._api_key},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise MarkingUnavailableError(f"data plane unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise MarkingUnavailableError(f"verify failed {resp.status_code}: {resp.text}")
        return resp.json()
