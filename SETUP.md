# Publishing setup — artemis-provenance-sdk

How this package gets to PyPI so customers can `pip install artemis-provenance-sdk`.
It mirrors the Node SDK's tokenless flow: GitHub Actions + **Trusted Publishing
(OIDC)** — no long-lived PyPI API token is stored anywhere.

Like the Node SDK, releases are **fully automated**: push Conventional Commits
to `main` and [python-semantic-release](https://python-semantic-release.readthedocs.io/)
does the rest — version bump, changelog, tag, GitHub Release, PyPI publish. No
manual release step.

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

Just push (or merge) **Conventional Commits** to `main`. The `Release & publish`
workflow then:

1. runs the tests;
2. runs **python-semantic-release**, which parses the commits since the last
   tag and decides the next version — `feat:` → minor, `fix:`/`perf:` → patch,
   `feat!:`/`BREAKING CHANGE:` → major; `docs:`/`chore:`/`test:`/`refactor:` →
   **no release** (the workflow ends there);
3. bumps `__version__` in `src/artemis_provenance_sdk/__init__.py` (the single
   source of truth — never bump it by hand), updates `CHANGELOG.md`, commits
   with `[skip ci]`, tags `v<version>` and creates the GitHub Release;
4. builds sdist + wheel from the tag and uploads to PyPI via OIDC
   (`pypa/gh-action-pypi-publish`), gated by the `pypi` environment.

So: `git commit -m "fix: handle 503 retry"` + push = a patch release on PyPI a
few minutes later. Commit discipline is the release process.

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
