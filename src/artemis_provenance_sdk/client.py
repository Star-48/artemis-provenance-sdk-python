"""Thin client for the Artemis Provenance data-plane marking API.

The SDK never marks locally — it calls the data-plane container the customer
runs in their own network, so content never leaves the VPC. Fail modes are
explicit: :class:`MarkingUnavailableError` is catchable so customer pipelines
choose fail-open vs fail-closed CONSCIOUSLY (spec §9).
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, TypedDict, Union

import httpx

__all__ = [
    "Client",
    "MarkedAsset",
    "MarkedText",
    "MarkingFailedError",
    "MarkingUnavailableError",
    "TEXT_VERDICTS",
    "VerifyResult",
    "VerifyTextResult",
]

TEXT_VERDICTS = ("exact-match", "canonical-match", "softbinding-recovered", "no-match")
"""Verdicts returned by :meth:`Client.verify_text`, strongest first."""


MARKING_FAILED_CODE = "marking_failed"
"""Machine-readable code the data plane returns when the required mark is missing."""


class MarkingUnavailableError(Exception):
    """The data plane could not be reached or could not mark the asset.

    Catch this to decide fail-open (ship unmarked — a compliance gap) vs
    fail-closed (block the asset). The choice is the customer's to make;
    document the implications either way.
    """


class MarkingFailedError(MarkingUnavailableError):
    """The data plane RAN and could not produce the REQUIRED mark.

    The required mark is modality-aware: the invisible watermark for image,
    video and audio; the detached signed manifest for text. (A failed optional
    zero-width soft binding is best-effort and never raises.)

    Deliberately a subclass of :class:`MarkingUnavailableError`: an unreachable
    data plane and a failed mark are the SAME compliance outcome — nothing was
    marked — so a pipeline that already chose fail-open or fail-closed for one
    behaves identically for the other with no code change. Catch this type
    specifically when you want the detail, or the unmarked output.

    Attributes:
        detail: The machine-readable failure body — ``code``, ``contentType``,
            ``requiredMark``, ``reason``, ``onMarkingFailure``, ``eventId``,
            ``payloadId``, ``sha256``, ``marks``, and ``warning`` when the
            output was returned anyway.
        unmarked: The UNMARKED :class:`MarkedAsset` / :class:`MarkedText`,
            populated ONLY under the tenant policy
            ``onMarkingFailure = return_unmarked``. It never comes back as a
            return value, so shipping unmarked content always takes an explicit
            ``except``. ``None`` under the default ``reject`` policy — there,
            no asset exists at all.
    """

    def __init__(
        self,
        message: str,
        *,
        detail: Optional[Dict[str, Any]] = None,
        unmarked: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.detail: Dict[str, Any] = detail or {}
        self.unmarked = unmarked

    @property
    def required_mark(self) -> Optional[str]:
        """``"watermark"`` (media) or ``"signed-manifest"`` (text)."""
        return self.detail.get("requiredMark")

    @property
    def event_id(self) -> Optional[str]:
        """The evidence event recording this failure — written either way."""
        return self.detail.get("eventId")

    @property
    def on_marking_failure(self) -> Optional[str]:
        """The tenant policy that decided it: ``reject`` | ``return_unmarked``."""
        return self.detail.get("onMarkingFailure")

    @property
    def reason(self) -> Optional[str]:
        return self.detail.get("reason")


def _marking_failure(resp: httpx.Response) -> Optional[Dict[str, Any]]:
    """A ``marking_failed`` body, or None for any other error payload.

    Keys on ``code``, not on the status: FastAPI's own request-validation 422
    carries ``detail`` and no ``code``, and must not be mistaken for a
    compliance refusal.
    """
    try:
        body = resp.json()
    except ValueError:
        return None
    if isinstance(body, dict) and body.get("code") == MARKING_FAILED_CODE:
        return body
    return None


def _failure_message(detail: Dict[str, Any]) -> str:
    return (
        f"required mark ({detail.get('requiredMark')}) could not be produced for "
        f"{detail.get('contentType')}: {detail.get('reason')} "
        f"[policy {detail.get('onMarkingFailure')}, event {detail.get('eventId')}]"
    )


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
        ai_generated: The AI-generation declaration recorded in the signed C2PA
            manifest, echoed back. ``True`` when you declared nothing — see
            :meth:`Client.mark_image`.
        digital_source_type: The IPTC digitalSourceType asserted, or ``None``
            when none was.
    """

    bytes: bytes
    event_id: str
    payload_id: int
    sha256: str
    marks: Dict[str, str]
    mime: str
    ai_generated: Optional[bool] = None
    digital_source_type: Optional[str] = None


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


@dataclass
class MarkedText:
    """Attested text returned by :meth:`Client.mark_text`.

    mark_text attests, it does not watermark — no robust post-hoc text
    watermark exists. The durable evidence is the registry record plus the
    detached signed manifest, not anything hidden in the text.

    Attributes:
        text: The text to publish — identical to the input unless the optional
            soft binding was applied.
        bytes: UTF-8 bytes of ``text`` (what ``sha256`` is computed over).
        event_id: Provenance event id recorded on the control plane.
        payload_id: Numeric payload id (registry pointer; embedded in the text
            only when the soft binding was applied).
        sha256: SHA-256 of the returned output bytes (hex).
        sha256_pre_embed: SHA-256 of the pre-embed bytes — ``None`` unless the
            soft binding was applied.
        text_canonical_hash: Canonical text hash (``textcanon.v1``) — the
            formatting-robust match key.
        canonicalization: Canonicalization algorithm id (``"textcanon.v1"``).
        marks: Frozen mark keys — ``c2pa`` is the detached signed manifest;
            ``watermark`` is the soft-binding slot (``"not-applicable"`` in
            record-only mode), NOT a text watermark.
        soft_binding: ``{"applied": bool, "scheme": "zwsb.v1" | None}``. The
            soft binding is strippable — normalization, sanitizers, retyping,
            or one free paste-through tool removes it.
        manifest_jws: Detached signed manifest (compact JWS, ES256, your KMS
            key). Portable — serve it alongside the text; losing it loses
            nothing evidentiary. Always present on a returned MarkedText; it
            is ``None`` only on the one attached to a
            :class:`MarkingFailedError`, where the missing manifest IS the
            failure.
        ai_generated: The AI-generation declaration recorded in the signed
            manifest, echoed back. ``True`` when you declared nothing — see
            :meth:`Client.mark_text`.
        digital_source_type: The IPTC digitalSourceType asserted, or ``None``
            when none was.
    """

    text: str
    bytes: bytes
    event_id: str
    payload_id: int
    sha256: str
    sha256_pre_embed: Optional[str]
    text_canonical_hash: str
    canonicalization: str
    marks: Dict[str, str]
    soft_binding: Dict[str, Any]
    manifest_jws: Optional[str]
    ai_generated: Optional[bool] = None
    digital_source_type: Optional[str] = None


class VerifyTextResult(TypedDict, total=False):
    """Shape of the dict returned by :meth:`Client.verify_text`.

    Keys:
        result: ``"matched"`` or ``"no-match"``.
        verdict: One of :data:`TEXT_VERDICTS` — ``"exact-match"``,
            ``"canonical-match"``, ``"softbinding-recovered"`` (a pointer was
            found but the content does not match — treat as unverified) or
            ``"no-match"``.
        method: ``"sha256"``, ``"textcanon"``, ``"payload"`` or ``None``.
        event: The matched provenance event, or ``None``.
        checks: Per-signal detail (``sha256``, ``textCanonical``,
            ``softBinding``).
        generation_watermark: Always ``{"status": "not-checked", ...}`` — no
            provider text-watermark detector is available to third parties.
        short_text: ``True`` when the canonical text is under 64 chars
            (weak-evidence caveat).
        notes: Fixed honesty notes for the verdict — a no-match proves nothing
            about origin.
        local: ``True`` — verification ran inside your network.
    """

    result: str
    verdict: str
    method: Optional[str]
    event: Optional[Dict[str, Any]]
    checks: Dict[str, Any]
    generation_watermark: Dict[str, str]
    short_text: bool
    notes: list
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
        ai_generated: Optional[bool] = None,
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
            ai_generated: Declare whether the content was generated by AI.
                It selects the signed manifest's IPTC ``digitalSourceType``:
                ``True`` -> trainedAlgorithmicMedia, ``False`` ->
                digitalCreation. Leave it ``None`` (the default) and nothing is
                sent, which the data plane resolves to **AI-generated**
                (trainedAlgorithmicMedia): this SDK marks the output of your
                generation pipeline, and the Article 50 disclosure is the
                point. Pass ``False`` explicitly for human-made,
                non-generative content.

        Raises:
            MarkingUnavailableError: The data plane is unreachable or refused
                to mark. Decide fail-open vs fail-closed consciously.
            MarkingFailedError: (a subclass of the above) the watermark — the
                required mark for media — could not be embedded. Under the
                default tenant policy nothing is returned; under
                ``onMarkingFailure = return_unmarked`` the UNMARKED asset is
                attached to the exception as ``err.unmarked``, so it can only
                be shipped deliberately.
        """
        data = {
            "app_id": app_id,
            "entity_id": entity_id,
            "title": (context or {}).get("title", filename),
        }
        if ai_generated is not None:
            data["ai_generated"] = "true" if ai_generated else "false"
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
            # Fail closed: the data plane refused because the watermark could
            # not be embedded. There is no asset to hand back.
            failure = _marking_failure(resp)
            if failure is not None:
                raise MarkingFailedError(_failure_message(failure), detail=failure)
            raise MarkingUnavailableError(f"mark failed {resp.status_code}: {resp.text}")

        d = resp.json()
        asset = MarkedAsset(
            bytes=base64.b64decode(d["marked"]),
            event_id=d["eventId"],
            payload_id=int(d["payloadId"]),
            sha256=d["sha256"],
            marks=d["marks"],
            mime=d["mime"],
            ai_generated=d.get("aiGenerated"),
            digital_source_type=d.get("digitalSourceType"),
        )
        # A 200 does NOT mean marked: under ``onMarkingFailure=return_unmarked``
        # the data plane returns the unmarked bytes, flagged. Never return them
        # — shipping unmarked content must take an explicit ``except``.
        if d.get("markingFailed"):
            failure = d.get("markingFailure") or {"code": MARKING_FAILED_CODE}
            raise MarkingFailedError(
                _failure_message(failure), detail=failure, unmarked=asset
            )
        return asset

    def mark_image_file(
        self,
        path: Union[str, "os.PathLike[str]"],
        *,
        app_id: str,
        entity_id: str = "default",
        context: Optional[Dict[str, str]] = None,
        ai_generated: Optional[bool] = None,
    ) -> MarkedAsset:
        """Convenience wrapper: read ``path`` and :meth:`mark_image` its bytes."""
        with open(path, "rb") as f:
            return self.mark_image(
                f.read(),
                app_id=app_id,
                entity_id=entity_id,
                context=context,
                filename=os.path.basename(os.fspath(path)),
                ai_generated=ai_generated,
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

    def mark_text(
        self,
        text: str,
        *,
        app_id: str,
        entity_id: str = "default",
        soft_binding: Optional[bool] = None,
        context: Optional[Dict[str, str]] = None,
        ai_generated: Optional[bool] = None,
    ) -> MarkedText:
        """Attest text: canonical hashes + KMS-signed detached manifest + registry event.

        mark_text attests, it does not watermark — no robust post-hoc text
        watermark exists. The reliable layer is the signed registry event and
        the detached manifest returned as :attr:`MarkedText.manifest_jws`; the
        optional zero-width soft binding is strippable — normalization,
        sanitizers, retyping, or one free paste-through tool removes it, so
        never rely on it surviving.

        The manifest declares the text AI-generated unless you pass
        ``ai_generated=False`` — see the argument below.

        Args:
            text: The finished text to attest (UTF-8).
            app_id: Your application id (groups events per app on the console).
            entity_id: Entity the text belongs to (per-entity provenance chain).
            soft_binding: ``True`` to embed the optional soft binding,
                ``False`` to skip it, ``None`` (default) to follow the tenant
                policy (record-only by default).
            context: Optional metadata strings; ``context["title"]`` is stored
                on the event, remaining keys (e.g. ``model``) go to the signed
                manifest's ``generator`` block.
            ai_generated: Declare whether the content was generated by AI.
                It selects the signed manifest's IPTC ``digitalSourceType``:
                ``True`` -> trainedAlgorithmicMedia, ``False`` ->
                digitalCreation. Leave it ``None`` (the default) and nothing is
                sent, which the data plane resolves to **AI-generated**
                (trainedAlgorithmicMedia): this SDK marks the output of your
                generation pipeline, and the Article 50 disclosure is the
                point. Pass ``False`` explicitly for human-made,
                non-generative content.

        Raises:
            MarkingUnavailableError: The data plane is unreachable or refused
                to mark. Decide fail-open vs fail-closed consciously.
            MarkingFailedError: (a subclass of the above) the detached SIGNED
                MANIFEST — the required mark for text — could not be signed.
                A failed optional soft binding never raises. Under
                ``onMarkingFailure = return_unmarked`` the unattested
                :class:`MarkedText` is attached as ``err.unmarked``.
        """
        data: Dict[str, str] = {"text": text, "app_id": app_id, "entity_id": entity_id}
        ctx = dict(context or {})
        title = ctx.pop("title", None)
        if title is not None:
            data["title"] = title
        if soft_binding is not None:
            data["soft_binding"] = "true" if soft_binding else "false"
        if ai_generated is not None:
            data["ai_generated"] = "true" if ai_generated else "false"
        if ctx:
            data["context_json"] = json.dumps(ctx)
        try:
            resp = httpx.post(
                f"{self._endpoint}/mark/text",
                data=data,
                headers={"x-api-key": self._api_key},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise MarkingUnavailableError(f"data plane unreachable: {exc}") from exc

        if resp.status_code == 503:
            raise MarkingUnavailableError(f"marking unavailable: {resp.text}")
        if resp.status_code != 200:
            # Text's required mark is the SIGNED MANIFEST; a failed optional
            # soft binding is not a marking failure and never lands here.
            failure = _marking_failure(resp)
            if failure is not None:
                raise MarkingFailedError(_failure_message(failure), detail=failure)
            raise MarkingUnavailableError(f"mark text failed {resp.status_code}: {resp.text}")

        d = resp.json()
        attested = MarkedText(
            text=d["text"],
            bytes=base64.b64decode(d["marked"]),
            event_id=d["eventId"],
            payload_id=int(d["payloadId"]),
            sha256=d["sha256"],
            sha256_pre_embed=d.get("sha256PreEmbed"),
            text_canonical_hash=d["textCanonicalHash"],
            canonicalization=d.get("canonicalization", "textcanon.v1"),
            marks=d["marks"],
            soft_binding=d.get("softBinding") or {"applied": False, "scheme": None},
            manifest_jws=d["manifestJws"],
            ai_generated=d.get("aiGenerated"),
            digital_source_type=d.get("digitalSourceType"),
        )
        # Unattested text under ``onMarkingFailure=return_unmarked`` reaches the
        # caller the same way an unmarked image does: raised, never returned.
        if d.get("markingFailed"):
            failure = d.get("markingFailure") or {"code": MARKING_FAILED_CODE}
            raise MarkingFailedError(
                _failure_message(failure), detail=failure, unmarked=attested
            )
        return attested

    def mark_text_file(
        self,
        path: Union[str, "os.PathLike[str]"],
        *,
        app_id: str,
        entity_id: str = "default",
        soft_binding: Optional[bool] = None,
        context: Optional[Dict[str, str]] = None,
        ai_generated: Optional[bool] = None,
    ) -> MarkedText:
        """Convenience wrapper: read ``path`` as UTF-8 and :meth:`mark_text` it."""
        with open(path, "r", encoding="utf-8") as f:
            return self.mark_text(
                f.read(),
                app_id=app_id,
                entity_id=entity_id,
                soft_binding=soft_binding,
                context=context,
                ai_generated=ai_generated,
            )

    def verify_text(self, text: str) -> VerifyTextResult:
        """Verify text LOCALLY via the data plane.

        The text never leaves your network; only ids/hashes are resolved
        against the control plane. Verdict ladder: ``exact-match`` ->
        ``canonical-match`` -> ``softbinding-recovered`` (a pointer was found
        but the content does not match the attested text — treat as
        unverified) -> ``no-match``. A no-match proves nothing about origin:
        unmarked, edited, paraphrased, translated, or third-party text all
        produce it, and no AI-vs-human inference is ever made.

        Returns:
            The provenance result — see :class:`VerifyTextResult`.

        Raises:
            MarkingUnavailableError: The data plane is unreachable or the
                verify call failed.
        """
        try:
            resp = httpx.post(
                f"{self._endpoint}/verify/text",
                data={"text": text},
                headers={"x-api-key": self._api_key},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise MarkingUnavailableError(f"data plane unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise MarkingUnavailableError(f"verify text failed {resp.status_code}: {resp.text}")
        d = dict(resp.json())
        if "generationWatermark" in d:
            d["generation_watermark"] = d.pop("generationWatermark")
        if "shortText" in d:
            d["short_text"] = d.pop("shortText")
        return d  # type: ignore[return-value]
