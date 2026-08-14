# Publishing setup — artemis-provenance-sdk

How this package gets to PyPI so customers can `pip install artemis-provenance-sdk`.
It mirrors the Node SDK's tokenless flow: GitHub Actions + **Trusted Publishing
(OIDC)** — no long-lived PyPI API token is stored anywhere.

The Node SDK publishes via semantic-release on every push to `main`; the Python
SDK publishes on a **GitHub Release** (the flow PyPI recommends for trusted
publishing). Same trust model, slightly more deliberate trigger.

## One-time setup (manual, ~10 minutes)

1. **Create the public GitHub repo** `Star-48/artemis-provenance-sdk-python`
   and push the contents of `sdk-python/` to its `main` branch (same split as
   `artemis-provenance-sdk-node`).

2. **PyPI account**: sign in at <https://pypi.org> (create an account with 2FA
   if you don't have one — PyPI requires 2FA for publishing).

3. **Add a *pending* trusted publisher** (the project doesn't exist on PyPI yet,
   so it's registered before the first publish):
   PyPI → your account → **Publishing** → **Add a new pending publisher** →
   GitHub, with exactly these fields:

   | Field | Value |
   |---|---|
   | PyPI project name | `artemis-provenance-sdk` |
   | Owner | `Star-48` |
   | Repository name | `artemis-provenance-sdk-python` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

   The first successful publish creates the PyPI project and converts the
   pending publisher into a normal trusted publisher automatically.

4. **Create the GitHub environment**: repo → Settings → Environments → New
   environment → name it `pypi`. (Optional but recommended: add yourself as a
   required reviewer so every publish needs a click of approval.)

That's it. No secrets to create: the workflow's `id-token: write` permission
lets PyPI verify the job's OIDC identity against the publisher configured above.

## How a release happens (every time)

1. Bump `__version__` in `src/artemis_provenance_sdk/__init__.py`
   (pyproject reads it dynamically — single source of truth) and merge to `main`.
2. GitHub → Releases → **Draft a new release** → tag `v<version>` (e.g.
   `v0.1.0`) → Publish release.
3. The `Publish to PyPI` workflow runs: tests → checks the tag matches
   `__version__` (a mismatch fails the job, so a stale version can never ship)
   → `python -m build` → `pypa/gh-action-pypi-publish` uploads via OIDC.

## What users do

```bash
pip install artemis-provenance-sdk
```

```python
from artemis_provenance_sdk import Client
```

## Local dev

```bash
cd sdk-python
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
python -m build   # sanity-check the sdist + wheel locally
```
