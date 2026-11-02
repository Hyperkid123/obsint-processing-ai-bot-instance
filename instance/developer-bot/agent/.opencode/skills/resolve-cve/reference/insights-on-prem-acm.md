# Insights on Prem ACM image (`lightspeed-services-acm/ocp-rules-rhel9`)

Full remediation workflow for CVEs reported against the productized ACM
recommendations image `lightspeed-services-acm/ocp-rules-rhel9`. It is built
from the public GitHub repository
[RedHatInsights/lightspeed-advisor-on-premise-ocp](https://github.com/RedHatInsights/lightspeed-advisor-on-premise-ocp)
(insights-on-prem) and is **layered on top of the OCP rules image**
`lightspeed-services-ocp/ocp-rules-rhel9` — see
@reference/rules-containers-public.md for that base image's workflow.

## Detection

The Jira summary names the image itself as the component:

```
CVE-YYYY-NNNNN lightspeed-services-acm/ocp-rules-rhel9: {package}: {title} [services-ccx-default]
```

Treat the CVE as this workflow when the component (text between the CVE ID
and the first `:`) matches, case-insensitively, any of:

- `lightspeed-services-acm/ocp-rules-rhel9` (exact product name)
- any component containing `lightspeed-services-acm`

**Match this before the bare `ocp-rules-rhel9` rule** — that shorter match
belongs to the OCP image built from `rules-containers`.

## Repository and pipeline routing

| Item | Value |
| --- | --- |
| Repository | `https://github.com/RedHatInsights/lightspeed-advisor-on-premise-ocp` (key `lightspeed-advisor-on-premise-ocp` in `project-repos.json`) |
| Bot fork | `git@github.com-bot:platex-rehor-bot/lightspeed-advisor-on-premise-ocp.git` |
| **Working branch** | **`main`** (GitHub default branch) — the PR is opened against `main` |
| Containerfile | `Dockerfile` at the repo root |
| Konflux component | `insights-on-prem` in the `obsint-processing-tenant` namespace; `.tekton/insights-on-prem-push.yaml` triggers on push to `main` |
| Built image | `quay.io/redhat-user-workloads/obsint-processing-tenant/insights-on-prem/insights-on-prem:<sha>` |
| Published image | `registry.redhat.io/lightspeed-services-acm/ocp-rules-rhel9` — tags like `<date>-N` (e.g. `2026.10.20-2`), `latest`; see the [catalog page](https://catalog.redhat.com/en/software/containers/lightspeed-services-acm/ocp-rules-rhel9/6a5a43e173093cb1777aac99) |

**The `public`-branch rule does NOT apply here.** It belongs to the OCP rules
image in `ccx/rules-containers` only. This repository has no role for a
`public` branch — explicitly fetch and branch from `upstream/main`, then open
the PR against `main`. Follow `/cve-workflow`'s repository preparation override
before cloning/preparing the checkout and on retries; `origin` is the bot fork.

This is a **public GitHub repository**, so the normal GitHub PR flow applies:
push to the bot fork, open the PR against `main`, and use `/wait-for-ci`
before any CI-passed review notification.

**Fork prerequisite**: the coordinator must confirm that the configured bot
fork exists and is accessible with the bot identity before starting the PR
flow. A 404 or denied access is a setup blocker, not permission to switch to
upstream pushes or another identity. Record the blocker and request maintainer
help through WatchDuty `needs_help`; do not create a fork without authorization.

## Image layers — determine where the vulnerable package comes from

The `Dockerfile` starts with:

```
FROM registry.redhat.io/lightspeed-services-ocp/ocp-rules-rhel9:<date-tag>
```

The ACM image starts from the OCP rules image, but its own install/cleanup
steps can replace or remove inherited packages. Scan the affected published
ACM image with
`syft registry.redhat.io/lightspeed-services-acm/ocp-rules-rhel9:<tag> --from registry -o json`
(record its resolved digest), then classify the affected package using the
Dockerfile and lockfiles for that build:

| Origin | Typical packages | Fix location |
| --- | --- | --- |
| **Controlled only by the base image** | Rules content and Python/RPM packages that the affected ACM build does not pin, install, upgrade, or replace | Fix in `ccx/rules-containers` **`public` branch** first — follow @reference/rules-containers-public.md. Once a fixed OCP image is published, bump the ACM `FROM` reference (see "Base image bump" below) |
| **ACM layer — Python deps** | FastAPI/uvicorn/SQLAlchemy/alembic etc. (the service app) | `requirements-in.txt` → `requirements.txt` (hashed Lightwell lockfile) in this repo |
| **Shared Python deps also controlled by ACM** | `charset-normalizer`, `markupsafe`, `msgpack`, `PyYAML` can already exist in the base and also be pinned by the ACM lockfile | Fix vulnerable ACM constraints/pins and regenerate its lockfile; an OCP base bump alone may leave or reinstall the vulnerable ACM version. Fix the OCP copy too if it remains vulnerable/ships in the resulting image |
| **ACM layer — RPMs** | `python3.12-psycopg2` (+ `libpq`) | `Dockerfile` `microdnf install` list + `rpms.in.yaml` → `rpms.lock.yaml` in this repo |

**Identify the actual base before comparing images**:

1. Resolve the affected ACM publication to a digest and find the corresponding
   source revision using its image metadata or build/release provenance. Read
   the `Dockerfile`, `requirements-in.txt`, `requirements.txt`, and RPM inputs
   at that revision, not today's `main`.
2. Determine the OCP `FROM` reference used by that build, preferably including
   its resolved base digest from build provenance. **Do not reuse the ACM
   publication tag as the OCP tag**: the two repositories have independent
   tags. A mutable `FROM` tag's current digest need not be the digest used by
   the affected build. If the actual base cannot be established, record the
   uncertainty and request help rather than guessing package ownership.
3. Scan that exact OCP base and compare package versions and installed paths
   with the affected ACM image. Inspect the ACM install commands and pins:
   finding the same vulnerable version in both images is not proof that ACM
   leaves it untouched. Check system and venv copies separately where present.
4. Select Case A for base-only ownership, Case B for ACM-owned packages, or
   both when both layers need remediation. Record the evidence and verify the
   final rebuilt/published ACM image, not just the corrected base image.

## Resolution workflow

### Case A — vulnerable package inherited from the base image

1. Fix the OCP image in `ccx/rules-containers` (`public` branch), or identify
   an existing verified fix, following @reference/rules-containers-public.md.
   **The ACM base-image bump is blocked until a
   fixed OCP image is published to registry.redhat.io** — record that
   dependency in Jira and in the prod-gate marker.
2. After the fixed OCP tag is visible in the catalog, bump the base image in
   this repo (see "Base image bump" below) so the ACM image is rebuilt on the
   fixed base.
3. Re-check ACM constraints and installed copies. If ACM also pins or
   reinstalls the vulnerable package, perform Case B as well; the base bump
   does not supersede ACM's lockfile. Verify the final ACM image is safe.

### Case B — vulnerable package in the ACM layer

1. **Python deps**: bump the pin in `requirements-in.txt`, then regenerate
   the hashed lockfile with `bash scripts/update_requirements.sh` (uv;
   resolves for linux/x86_64/Python 3.12 from any host, but the Lightwell
   validated index may require authentication — if credentials are
   unavailable, report the blocker and send the WatchDuty `needs_help`
   notification; do not hand-edit the hashed lockfile). Mind the README
   caveats: `--upgrade` re-resolves every package (review the diff), and the
   Lightwell index is curated (the fixed version may not be carried yet).
2. **RPMs**: edit the `Dockerfile` `microdnf install` list / `rpms.in.yaml`
   as needed, then regenerate `rpms.lock.yaml` with
   `bash scripts/update_rpm_lockfile.sh` inside a `linux/amd64` UBI9
   container. The `FROM` image is the rpm-lockfile context image, so a stale
   base tag locks wrong versions.
3. **Verify locally**: `podman build .` (non-hermetic builds fall back to
   public PyPI), then `syft`/`grype` the local image to confirm the package
   moved outside the vulnerable range.

### Base image bump (after a fixed OCP image is published)

1. Update `FROM registry.redhat.io/lightspeed-services-ocp/ocp-rules-rhel9:<new-tag>`
   in `Dockerfile` to the fixed published tag.
2. Regenerate `rpms.lock.yaml` — the base image is the rpm-lockfile context
   image, so the lockfile must match the new base (needs a
   `registry.redhat.io` pull secret in `scripts/.dockerconfig.json` for
   `skopeo`).
3. Check ACM's Python lockfile for any affected shared package. If its pin
   would retain/reinstall a vulnerable version, update the matching input
   constraint and regenerate `requirements.txt` with the helper. Verify the
   final installed version after rebuilding.
4. Check for an existing base-bump PR before creating one. After a successful
   rules release, `ccx-rules-releaser`'s `trigger-insights-on-prem` job starts
   the child workflow in `.gitlab/standalone-pipelines/insights-on-prem.yaml`;
   `INSIGHTS_ON_PREM_PIPELINE` selects a standalone run for recovery/testing.
   The child refreshes Python and RPM lockfiles and opens the ACM PR, but is
   asynchronous and allowed to fail independently of the parent. Catalog
   publication alone does not guarantee that it ran or succeeded. Reuse a
   matching PR; if none exists, follow this manual workflow or request
   `needs_help` for the specific blocker. Verify the resulting image either way.

### PR, build, and release

1. Open a PR against `main` from the bot fork, add the standard attribution
   comment, link it in Jira, transition to "Code Review".
2. This is a GitHub PR: use `/wait-for-ci` and its guarded notification flow.
   Konflux also runs the hermetic build on PRs and pushes; a failing
   hermetic build is a CI failure like any other.
3. After merge, the push pipeline builds
   `quay.io/redhat-user-workloads/obsint-processing-tenant/insights-on-prem/insights-on-prem:<merge-sha>`.
   Wait ~20 minutes, then verify with `skopeo inspect` / `syft`.

## Production gate — Konflux release + catalog publication

- **No app-interface deployment**: never create an app-interface MR for this
  image and do not add the `repo:app-interface` label.
- Production means the fixed build is **released by Konflux** to
  `quay.io/redhat-services-prod/obsint-processing-tenant/insights-on-prem/insights-on-prem`
  and **published** to
  `registry.redhat.io/lightspeed-services-acm/ocp-rules-rhel9` (visible on
  the catalog page).
- If the release/publish step requires a manual action the bot cannot
  perform, send the WatchDuty `needs_help` notification with the blocker and
  keep the ticket in "Code Review".
- Close only after the fixed tag is visible in the catalog (clear the prod
  gate with the standard marker sequence). Record the published digest and
  verify its affected package versions, including ACM-owned shared pins;
  catalog visibility alone does not prove the fix is present. For Case A or
  combined Case A/B CVEs, also verify that the published ACM build uses the
  fixed OCP base, using that build's `FROM` reference and provenance.
