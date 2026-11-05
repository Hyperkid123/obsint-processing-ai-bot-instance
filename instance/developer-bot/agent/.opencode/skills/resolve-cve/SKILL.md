---
name: resolve-cve
description: >-
  Resolve a CVE vulnerability issue from Jira. Reads the
  CVE details, assesses impact, and either marks "not
  affected" with a Jira comment and transition, bumps the
  affected dependency, or implements a code fix. Use when
  the user says "cve", "resolve CVE", or provides a CVE
  Jira issue.
---

# resolve-cve

The user provides a Jira key (e.g., `CCXDEV-12345`) or a Jira
URL. If no specific issue is given, find CVEs to triage by
searching the current sprint in the **CCX Core - Processing**
(CCXDEV) project.

## Pre-requisites

When running as the autonomous CVE workflow, follow `/cve-workflow` for
notifications. For GitHub application PRs, run `/post-pr` with Slack skipped and
use `/wait-for-ci`, including later cycles. Running CI defers notification;
unavailable verification uses the workflow's `ci-unverified` review request.
For app-interface GitLab MRs, send `production-update` immediately after creation
with `<!subteam^S043UGRST2L>`, without waiting for CI or claiming it passed.
For rules-containers GitLab MRs, use the workflow's dedicated `rules-image-mr`
notice instead, with the same WatchDuty mention and delivery-retry rules.

The user should have installed the `acli` tool:
https://developer.atlassian.com/cloud/acli/guides/install-acli/

You can check if it's installed by running:
`acli --version`

The authentication should be set up. If you ever find any issues with
the authentication, tell the user to follow
https://docs.google.com/document/d/1ilv5NgMS06SK7kS6jXtaELdxE9Nbh_8-nZcEeC9bXPI.

Full documentation for the CLI can be found in https://developer.atlassian.com/cloud/acli/.

The user also needs `syft` installed to be able to inspect the images. Please
ask the user to follow https://github.com/anchore/syft if they don't have it installed.

## Step 0: Find CVEs to triage

Use `acli` to find the CVEs to triage:
```
acli jira workitem search --jql 'project = CCXDEV
  AND type = Vulnerability
  AND sprint in openSprints()
  AND statusCategory = "To Do"
  AND assignee = currentUser()
  ORDER BY priority DESC' --fields key,summary
```

## Step 1: Read the CVE Issue

Now, for each CVE, fetch the issue via
`acli jira workitem view {Jira Key}`

and parse the data from these fields:

- **CVE ID** — embedded in the `summary` field, with this format
  `CVE-YYYY-NNNNN {Component}: {Package}: {Title} [services-ccx-default]` e.g.,
  `CVE-2026-32285 parquet-factory: github.com/buger/jsonparser: Denial of Service via malformed JSON input [services-ccx-default]`
- **Component** — the first token after the CVE ID. It is usually a repo
  name, but for image CVEs it can be a full image/product name such as
  `lightspeed-services-ocp/ocp-rules-rhel9` (contains slashes — do not split
  it on `/`).
- **Affected package** — mentioned in the description's
  `Flaw:` section (the description starts with boilerplate
  "Security Tracking Issue", "Do not make this issue
  public" — skip to the flaw text). The description stops at the `~~~` separator.

Then look up severity and fix details externally:

- **CVSS score** — open the CVE's NVD entry with the configured
  `chrome-devtools` browser tools to get the severity rating.
- **Fixed version and references** — browse the authoritative sources below
  or use the proxy-backed `gh` CLI for public GitHub advisories and fix commits;
  collect and record these links for later use in the Jira comment:
  - NVD entry URL (e.g., `https://nvd.nist.gov/vuln/detail/CVE-YYYY-NNNNN`)
  - Language-specific advisory (e.g., Go: `https://pkg.go.dev/vuln/GO-YYYY-NNNN`,
    Python: the relevant GitHub Security Advisory)
  - Upstream fix (PR or commit URL)

  These links are the **proof** for the vulnerable version range
  and the fixed version. Always verify the fixed version from
  an authoritative source (NVD, language advisory DB, or the
  upstream PR/release notes) — do not rely solely on web search
  summaries.

If a required source cannot be retrieved, report that blocker rather than
guessing severity, affected versions, or fixed versions.

If the issue is missing a CVE ID or the affected package
is unclear from the flaw text, ask the user to clarify.

## Step 2: Assess Impact

**Special case — productized rules images**: match the component
case-insensitively in this order:

1. If it contains `lightspeed-services-acm`, use the ACM workflow.
2. Otherwise, if it contains `lightspeed-services-ocp` or
   `ocp-rules-rhel9`, use the OCP workflow.

Follow the matching reference instead of the generic repository and
production-update flow below:

- `lightspeed-services-acm/ocp-rules-rhel9` →
  @reference/insights-on-prem-acm.md (repo
  `RedHatInsights/lightspeed-advisor-on-premise-ocp`, GitHub — PR against
  `main`, no `public` branch in this repository; layered on the OCP image —
  base-only packages are fixed in `rules-containers` first; ACM-owned shared
  pins may need an ACM fix too; released by Konflux, published to registry.redhat.io)
- `lightspeed-services-ocp/ocp-rules-rhel9` →
  @reference/rules-containers-public.md (repo `ccx/rules-containers`, GitLab,
  **`public` branch**; production = registry.redhat.io catalog publication)

Both images have **no app-interface deployment** — the production gate is
catalog publication, not an app-interface MR.

Determine whether this project is affected:

1. **Check if the package is a dependency** — Use @reference/repos.yaml
   to find the repository for this component. If the component name is not
   among the repositories, ask the user to clarify.
   Match case-insensitively (Jira may say `NLTK`, lock
   file has `nltk`). If not present at all, the project
   may not be affected. However, it can be installed at build time, so please
   check also in @reference/images.yaml.
2. **Check the installed version** — if found, compare the installed version
   with the vulnerable version range.
3. **Check if the vulnerable code path is reachable** — if
   the CVE targets a specific feature or module of the
   package, search the codebase for imports and usage of
   that feature. If the project never calls the affected
   API, it may be installed at build time.
4. **Check transitive dependencies** — if the package isn't
   a direct dependency, check whether it appears as a
   transitive dependency in the dependency tree. Trace which direct
   dependency pulls it in. Check things like `uv.lock` or `go.sum`.
5. **Check if the vulnerability is introduced at build time** — find the
   exact version using `syft`. For example:
   ```
   syft quay.io/redhat-services-prod/obsint-processing-tenant/parquet-factory/parquet-factory:latest
   ```
   or appending `-o json` if that makes it easier to parse.
   Please make sure to use `--from registry`, otherwise it won't pull the latest
   version.
   If the package is there, it means it is installed at build time and may need
   to be fixed by a rebuilt or enforcing a newer version in the Dockerfile.
   Otherwise, the component is not affected.

For `registry.redhat.io` images, load @reference/redhat-registry-proxy.md and
use its Skopeo-to-OCI workflow before scanning with Syft. Direct Syft registry
access does not use the team proxy mapping.

## Step 3: Present Assessment

Present the finding to the user clearly:

```
CVE Assessment: {CVE-ID}

Package: {package name}
Vulnerable versions: {range}
Installed version: {version}
Direct dependency: {yes/no — if no, pulled in by {parent}}

Verdict: {NOT AFFECTED / AFFECTED — bump needed / AFFECTED — code change needed}

Reasoning:
- {why this verdict — e.g., "package not in dependency tree",
  "installed version is outside vulnerable range",
  "vulnerable API is not used by this project",
  "project uses the affected code path in module X"}
```

**GATE — do not proceed without user acknowledgment.**
The user may have context that changes the verdict
(e.g., the package is used indirectly, or the feature
is enabled in production but not in tests). Present the
assessment and stop. Only continue after explicit "go".

## Step 4: Resolve

Based on the verdict and user acknowledgment:

### Path A: Not Affected

For productized rules image CVEs, use the matching reference to verify the
published registry.redhat.io image; these images have no app-interface
deployment. Do not use the app-interface-specific checks or actions below for
those tickets. The generic app-interface steps in this path apply to other
components only.

Before closing, check what image is running in production:

1. **Check production image in app-interface**:
   - Open the `app-interface` repository
   - Find the component's saas file or deployment config:
     ```
     find data/services/ -name "*.yml" -o -name "*.yaml" | xargs grep -l "<component>"
     ```
   - Extract the `ref:` or image tag currently deployed

2. **If production image matches `:latest` or is also not affected**:

   Add a comment to the Jira issue via `acli`:

   ```
   acli jira workitem comment create --key {Jira Key} --body '{comment text}'
   ```

   Comment text:

   ```
   **Assessment: Not Affected**

   {CVE-ID} targets {package} versions {range}.

   {Reason — one of:}
   - Package is not in the dependency tree.
   - Installed version ({version}) is outside the
     vulnerable range.
   - The vulnerable code path ({specific API/module}) is
     not used by this project.

   Production image verified: up to date.

   References:
   - {NVD URL}
   - {Language advisory URL}
   - {Upstream fix PR/commit URL}

   No action required.
   ```

   Transition the issue to **Closed**:

   ```
   acli jira workitem transition --key {Jira Key} --status "Closed" --yes
   ```

   If "Closed" fails, try "Done" or "Won't Do" as the
   status value. Use `--yes` to skip the confirmation prompt.

3. **If production image is outdated (older ref, may be affected)**:

   Scan the production image to confirm:
   ```
   syft quay.io/redhat-services-prod/obsint-processing-tenant/<component>/<component>:<production-ref> --from registry -o json
   ```

   If the vulnerable package exists at a vulnerable version in the production image:

   a. Create an MR in app-interface to update the image reference to the latest tag:
      - Push to the app-interface fork (from `project-repos.json`)
      - Use the shared `/push-and-pr` helper from the app-interface checkout (GitLab API with the configured fork and upstream)
      - Title: `Update <component> image to resolve <CVE-ID>`
      - Description: Include CVE details, production vs latest scan comparison

   b. Add Jira comment:
      ```
      **Assessment: Not Affected (latest image)**
      **Production Image: Outdated — Update Required**

      {CVE-ID} targets {package} versions {range}.

      Latest image: {version or "not present"} — NOT AFFECTED
      Production image ({production-ref}): {version} — AFFECTED

      The latest built image is clean, but production runs an older
      image that is still affected.

      Action: Created app-interface MR to update production image.
      MR: {MR_URL}

      References:
      - {NVD URL}
      - {Language advisory URL}
      - {Upstream fix PR/commit URL}
      ```

   c. Do NOT close the ticket — transition to "Code Review" instead.
      The ticket stays open until the production image is updated.

### Path B: Dependency Bump

Check in the repository pull requests when was the last time the package was bumped.
If it was more than 30 days ago, ask the user if they want to bump the package.
If they do, please help them with the process.

After the bump, verify the new version installed in the `latest` image is
outside the vulnerable range. You may need to wait for the user to merge
the PR.

If the latest release is still vulnerable, stop and tell the user — no fix is
available upstream yet.

Then add a Jira comment via `acli`:

```
acli jira workitem comment create --key {Jira Key} --body '{comment text}'
```

Comment text:

```
**Resolution: Dependency bumped**

{CVE-ID} targets {package} versions {range}.
Bumped {package} from {old version} to {new version}.

References:
- {NVD URL}
- {Language advisory URL}
- {Upstream fix PR/commit URL}

Lint/types/tests: passing.
```

Please add any links or even terminal output, both command and stdout, that would prove your statement is true.

Do NOT close the ticket yet — proceed to the production image update below.

### Path C: Code Change (Rare)

1. Explain to the user what code change is needed and why.
   This is unusual — confirm the approach before
   implementing.
2. Add a Jira comment summarizing the code change
   (`acli jira workitem comment create`).

Do NOT close the ticket yet — proceed to the production image update below.

### Paths B & C: Production Image Update (required before closing)

After the repo PR is merged, the ticket is NOT done until production is also updated.

**Exception — productized rules images** (`lightspeed-services-ocp/ocp-rules-rhel9`
and `lightspeed-services-acm/ocp-rules-rhel9`): these images are not deployed
via app-interface. Production means the fixed image is released and published
to `registry.redhat.io` (see @reference/rules-containers-public.md and
@reference/insights-on-prem-acm.md). Never create an app-interface MR for
them. **Skip the generic app-interface steps below** and follow the applicable
reference for the image build, release, and catalog-publication gate. For an
inherited-package ACM CVE, also verify the ACM `Dockerfile` `FROM` references
the fixed OCP base for the published build using its source revision and
provenance, and verify the final ACM image's affected package versions.

### Standard components: app-interface production update

The following generic app-interface procedure applies only to components
other than the productized rules images above.

The image needs time to build in Konflux and appear in Quay.

1. **Wait for image build**:
   - After the PR is merged, wait **20 minutes** for the
     Konflux pipeline to build and push the new image to Quay
   - Then verify the image is available:
     ```
     skopeo inspect docker://quay.io/redhat-services-prod/obsint-processing-tenant/<component>/<component>:latest
     ```
   - If the image is not available after 20 minutes, wait
     another **20 minutes** and check again
   - If still not available after the second wait (40 minutes total), send the
     `image-build-timeout` notification through the CVE workflow's shared Slack
     webhook and stop. Use the `/slack-notify` wrapper with
     `SLACK_NOTIFY_MODE=immediate` and the configured `SLACK_WEBHOOK_URL`, include
     `<!subteam^S043UGRST2L>`, and never override the destination.

2. **Check production image in app-interface**:
   - Open the `app-interface` repository
   - Find the component's saas file or deployment config:
     ```
     find data/services/ -name "*.yml" -o -name "*.yaml" | xargs grep -l "<component>"
     ```
   - Extract the `ref:` or image tag currently deployed

3. **If production already uses the fixed image** (ref matches
   the merged commit or a newer build):
   - Ask user about Jira transition to "Closed" or "Done"

4. **If production uses an older image**:
   - Create an MR in app-interface to update the image
     reference to the newly built fixed version:
     - Push to the app-interface fork (from `project-repos.json`)
     - Use the shared `/push-and-pr` helper from the app-interface checkout (GitLab API with the configured fork and upstream)
     - Title: `Update <component> image to fix <CVE-ID>`
     - Description: Include CVE details, link to merged
       application PR, grype/syft scan results
   - Update Jira comment with the MR link
   - Transition ticket to "Code Review" (not Closed)
   - Tell the user: ticket stays open until the
     app-interface MR is merged and production is updated

## Step 5: Report

```
CVE {CVE-ID} resolved for {story_id}.

Verdict: {Not Affected / Bumped {package} to {version} / Code fix applied}
Jira: {commented / commented + transitioned to {status}}

{If files changed:}
Files changed:
  - {list files}

Ready to commit.
{End if}
```

If the user wants a commit (Path B or C), use message:

```
fix: resolve {CVE-ID} — bump {package} to {version}
```

or for code changes:

```
fix: resolve {CVE-ID} — {brief description}
```

## Constraints

- **User acknowledgment required** — never act on the
  verdict without the user confirming the assessment.
  They may know things the codebase analysis cannot reveal.
- **Jira transitions** — Path A (Not Affected) transitions
  to Done/Closed only after verifying production image is
  also not affected (see Path A step 2-3). For Paths B and C,
  do NOT close until the applicable production gate is complete:
  app-interface MR for standard components, or Konflux release and
  registry.redhat.io catalog publication for productized rules images
  (including verification of the published ACM build's OCP base and final
  affected package versions for inherited package CVEs).
- **Minimal changes** — bump only the affected package,
  not all dependencies.
- **Verify after every change** — lint, types, and unit
  tests must pass before declaring done.
- **Do not downplay severity** — if the project is
  affected, say so clearly. Do not stretch "not affected"
  reasoning to avoid work.
