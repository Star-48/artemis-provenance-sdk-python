# artemis-provenance-sdk

Thin Python client for the **Artemis Provenance data plane** — mark and verify
AI-generated media (image · video · audio) for EU AI Act Article 50(2)
compliance. The SDK contains **no marking logic**; it serializes calls to the
data-plane container the customer runs in their own network, so content never
leaves the VPC.

```bash
pip install artemis-provenance-sdk
```

## Usage

```python
from artemis_provenance_sdk import Client, MarkingUnavailableError

pv = Client(endpoint="http://provenance-dp.internal:8080", api_key="...")

# Mark an asset at the end of your generation pipeline.
try:
    marked = pv.mark_image(image_bytes, app_id="avatar-studio",
                           context={"title": "Generated avatar"})
    # marked.bytes  -> the marked output to ship
    # marked.event_id, marked.payload_id, marked.sha256, marked.marks
except MarkingUnavailableError:
    # Decide fail-open (ship unmarked — a compliance gap) vs fail-closed.
    ...

# Verify locally — content never leaves your network; only the id is resolved.
result = pv.verify(image_bytes, content_type="image")
# result["result"] == "matched" | "no-match", result["event"], result["checks"], result["local"]
```

There is also a file convenience wrapper:

```python
marked = pv.mark_image_file("out/hero.png", app_id="avatar-studio")
```

## Fail modes

`MarkingUnavailableError` is catchable so your pipeline chooses fail-open vs
fail-closed **consciously** — document the compliance implications of each.

## Typing

The package ships a `py.typed` marker; `Client`, `MarkedAsset` and
`VerifyResult` are fully typed for editor/mypy support.

## Releasing (maintainers)

See [SETUP.md](./SETUP.md) for the one-time PyPI Trusted Publishing setup and
the per-release flow.

## License

MIT
