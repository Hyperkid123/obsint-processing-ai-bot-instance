# obsint-processing-ai-bot-instance

Ctibor is an autonomous developer agent that picks groomed Jira tickets,
implements code changes, opens PRs/MRs, and maintains them through the review
process without human intervention.

It is an instance of the [platform-frontend-ai-dev](https://github.com/RedHatInsights/platform-frontend-ai-dev)
framework (codenamed **Rehor**), customized for the CCX Processing team.

For an overview of how Ctibor works (architecture, priority system) and how to
assign tasks via Jira, see the
[CCX Docs — Ctibor section](https://ccx.pages.redhat.com/ccx-docs/docs/processing/ctibor/).

## Table of Contents

- [Personas](#personas)
- [Skills](#skills)
- [Persona vs Skill](#persona-vs-skill)
- [Configuration](#configuration)
- [Deployment](#deployment)
- [Build](#build)

## Personas

Personas are domain-specific behavioral prompts that guide Ctibor's approach to
different types of work. They provide coding standards, test commands,
conventions, and workflows tailored to a specific technology or domain.

### How Personas Work

Personas are stored as `prompt.md` files under
[`instance/my-config/agent/personas/<name>/`](instance/my-config/agent/personas/).

Ctibor **dynamically selects** the appropriate persona based on the ticket
description and the repository's tech stack. For example:

- A repo with `package.json` triggers the frontend persona
- A repo with `go.mod` triggers a backend persona
- A CVE ticket triggers the CVE persona

Personas are not hardcoded to specific repositories.

### Creating a New Persona

1. Create a directory under `instance/my-config/agent/personas/` with the
   persona name:
   ```
   instance/my-config/agent/personas/my-new-persona/
   ```

2. Add a `prompt.md` file with the behavioral instructions. A good persona
   should cover:

   - **Tech stack description** — languages, frameworks, key dependencies
   - **Development commands** — how to build, lint, test, and verify
   - **Coding conventions** — style, patterns, imports
   - **Workflow steps** — how to approach common tasks (dependency updates,
     bug fixes, etc.)
   - **Jira integration** — comment templates for assessment and resolution
   - **PR attribution** — the bot's identity line for PRs
   - **Production image updates** — app-interface MR workflow if applicable

3. Commit and push. Ctibor picks up the new persona on its next cycle.

## Skills

Skills are reusable, structured workflows that provide step-by-step procedures
for specific tasks. They complement personas by adding concrete recipes on top
of broad behavioral guidelines.

### How Skills Work

Skills are stored under [`instance/my-config/agent/skills/<name>/`](instance/my-config/agent/skills/)
and contain:

- `SKILL.md` — a structured workflow document with frontmatter (name,
  description, trigger conditions)
- `reference/` — supporting data files used by the skill

The bot invokes a skill when the ticket or context matches the trigger
conditions defined in the skill's frontmatter.

### Upstream Skills

The [platform-frontend-ai-dev](https://github.com/RedHatInsights/platform-frontend-ai-dev)
framework provides additional built-in skills that are available to all
instances:

| Skill | Purpose |
|-------|---------|
| `/triage` | Pre-fetches all active tasks, PR/MR statuses, CI results, reviews, Jira comments |
| `/new-work` | Fetches unassigned sprint candidates with full context |
| `/claim-ticket` | Claims a Jira ticket (assign, transition, add to sprint) |
| `/push-and-pr` | Pushes branch and creates PR/MR via API |
| `/post-pr` | Post-PR actions (Jira transition, comments) |
| `/wrap-up` | Post-merge cleanup (archival, Jira transition, Slack, branch deletion) |
| `/slack-notify` | Posts notifications to Slack (48h cooldown per ticket) |
| `/auto-fork` | Auto-forks repos under the bot account |

### Creating a New Skill

1. Create a directory under `instance/my-config/agent/skills/`:
   ```
   instance/my-config/agent/skills/my-new-skill/
   ```

2. Add a `SKILL.md` file with frontmatter and workflow steps:
   ```markdown
   ---
   name: my-new-skill
   description: Short description of what this skill does
   triggers:
     - keyword1
     - keyword2
   ---

   # My New Skill

   ## Step 1: Gather Context
   ...

   ## Step 2: Perform Action
   ...

   ## Step 3: Report Results
   ...
   ```

3. Optionally add a `reference/` directory with supporting data files (YAML,
   JSON, etc.) that the skill's workflow references.

4. Commit and push to the instance repository.

## Persona vs Skill

| Aspect | Persona | Skill |
|--------|---------|-------|
| **Purpose** | Broad behavioral guidelines for a type of work | Specific step-by-step procedure for a defined task |
| **Scope** | Covers an entire domain (e.g., "frontend maintenance") | Covers a single operation (e.g., "resolve a CVE") |
| **Selection** | Auto-selected based on repo tech stack and ticket | Invoked explicitly as a slash command or by matching trigger conditions |
| **Format** | Free-form markdown prompt | Structured workflow with frontmatter (name, description, triggers) |
| **Composition** | A persona can reference skills | A skill runs within the context of the active persona |

In short: the persona sets the *mindset*, the skill provides the *recipe*.

## Configuration

Ctibor's configuration lives under the
[`instance/my-config/agent/`](instance/my-config/agent/) directory.

### Configuration Files

| File | Purpose |
|------|---------|
| `project-repos.json` | Maps repository names to bot fork URLs and upstream URLs. Each `repo:<name>` Jira label must match a key in this file. |
| `mcp.json` | Configures MCP (Model Context Protocol) servers. Currently only `mcp-atlassian` for Jira integration. |
| `personas/<name>/prompt.md` | Domain-specific behavioral prompts. See [Personas](#personas). |
| `skills/<name>/SKILL.md` | Structured workflows. See [Skills](#skills). |
| `skills/<name>/reference/` | Supporting data files for skills. |

### project-repos.json

This file maps the `repo:<name>` Jira labels to repository URLs. Each entry
includes the bot's fork and the upstream repository:

```json
{
  "insights-results-aggregator": {
    "fork": "https://github.com/platex-rehor-bot/insights-results-aggregator.git",
    "upstream": "https://github.com/RedHatInsights/insights-results-aggregator.git",
    "host": "github"
  },
  "app-interface": {
    "fork": "https://gitlab.cee.redhat.com/platex-rehor-bot/app-interface.git",
    "upstream": "https://gitlab.cee.redhat.com/service/app-interface.git",
    "host": "gitlab"
  }
}
```

The `host` field distinguishes GitHub repos (`gh` CLI) from GitLab repos
(`glab` CLI).

### mcp.json

Configures external tool servers the bot can interact with:

```json
{
  "mcpServers": {
    "mcp-atlassian": {
      "url": "${JIRA_MCP_URL}"
    }
  }
}
```

### Adding a New Repository

1. Add an entry to `project-repos.json`:
   ```json
   {
     "my-new-repo": {
       "fork": "https://github.com/platex-rehor-bot/my-new-repo.git",
       "upstream": "https://github.com/RedHatInsights/my-new-repo.git",
       "host": "github"
     }
   }
   ```

2. Ensure a matching persona exists or that an existing persona covers the
   repo's tech stack.

3. Commit and push. The bot will recognize `repo:my-new-repo` labels on the
   next cycle. Forks are created automatically by the bot when it starts
   working on a ticket and no fork exists yet.

## Deployment

Ctibor is deployed on OpenShift via Konflux CI/CD pipelines, with production
image references managed through app-interface.

### Build Pipeline

The build is defined in `.tekton/`:

- **Push pipeline** — triggers on push to `master`. Builds a container image
  from `dev-bot/Dockerfile.runner` and pushes to:
  ```
  quay.io/redhat-user-workloads/obsint-processing-tenant/obsint-processing-ai-bot-instance/obsint-processing-ai-bot-instance:<revision>
  ```
  Runs security scans: Clair, Snyk SAST, ClamAV, and others.

- **PR pipeline** — triggers on PRs to `master`. Same build process but images
  expire after 5 days.

### Deployment Template

The OpenShift deployment template lives at `deploy/template.yaml`. Key
parameters:

| Parameter | Description | Default |
|-----------|-------------|---------|
| `IMAGE` | Production container image | `quay.io/redhat-services-prod/obsint-processing-tenant/obsint-processing-ai-bot-instance` |
| `BOT_LABEL` | Jira label the bot polls for | `obsint-processing-ai` |
| `BOT_BOARD_NAME` | Jira board name | `CCX Core - Processing` |
| `BOT_SPRINT_PREFIX` | Sprint naming prefix | `CCXDEV Sprint` |
| `REPLICAS` | Number of bot replicas | `0` (must be scaled up explicitly) |

The template creates a Deployment and a NetworkPolicy that restricts egress to
only the proxy, memory server, and OpenShift DNS.

### Shared Infrastructure

Ctibor connects to shared infrastructure deployed by the primary
platform-frontend-ai-dev instance:

| Service | Port | Purpose |
|---------|------|---------|
| `devbot-proxy` | 3128 | Squid proxy (domain allowlist) |
| `devbot-proxy` | 9090 | Executor (gRPC policy engine) |
| `devbot-proxy` | 8443 | Vertex AI auth proxy |
| `devbot-proxy` | 8444 | Jira MCP (mcp-atlassian) |
| `devbot-memory-server` | 8080 | Task tracking + RAG memory |

Secrets are sourced from Vault via `devbot-secrets` and include Git identity,
Jira credentials, and SSO tokens. The bot container itself has **no direct
access to secrets**.

### App-Interface Deploy File

The app-interface SaaS deploy configuration for Ctibor lives at:

[`data/services/insights/platform-frontend-ai-dev/obsint-deploy.yaml`](https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/data/services/insights/platform-frontend-ai-dev/obsint-deploy.yaml)

This is a `saas-file-2` resource that defines how Ctibor is deployed. Key
aspects:

- **Namespace:** shares the stage namespace with the primary
  platform-frontend-ai-dev deployment (`hcmais01ue1`)
- **Resource template:** points to `deploy/template.yaml` in this repository
- **Image:** `quay.io/redhat-services-prod/obsint-processing-tenant/obsint-processing-ai-bot-instance/obsint-processing-ai-bot-instance`
- **Managed resource types:** Deployment, Service, Route, NetworkPolicy,
  ScaledObject (KEDA)

#### Deploy Parameters

| Parameter | Value |
|-----------|-------|
| `BOT_NAME` | `devbot-obsint-processing` |
| `BOT_LABEL` | `obsint-processing-ai` |
| `BOT_INSTANCE_ID` | `Ctibor Šťastný z Čachtíc` |
| `BOT_REPLICAS` | `0` (scaled up explicitly) |
| `BOT_CONFIG_REPO` | `https://github.com/RedHatInsights/obsint-processing-ai-bot-instance.git` |
| `BOT_CONFIG_PATH` | `instance/my-config` |
| `GCP_PROJECT_ID` | `hcc-platform-agentic-sdlc` |
| `VERTEX_ALLOWED_MODELS` | `claude-sonnet-4-6, claude-opus-4-6, claude-haiku-4-5` |

The `ref` and `BOT_IMAGE_TAG` fields are set to the git commit SHA of the
deployed version.

### Deploying a New Version

1. **Merge your changes** to the `master` branch of this repository.

2. **Wait for the Konflux push pipeline** to build and push the new image.
   The image tag will be the git commit SHA.

3. **Update the image reference in app-interface.** Edit the
   [obsint-deploy.yaml](https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/data/services/insights/platform-frontend-ai-dev/obsint-deploy.yaml)
   file — update both `ref` and `BOT_IMAGE_TAG` to the new commit SHA and
   open an MR.

4. **Get the app-interface MR reviewed and merged.** App-interface MRs always
   require human review and are never auto-merged.

5. **Verify the deployment.** Check the OpenShift pod logs and the memory server
   dashboard for the bot's next cycle.

## Build

```bash
git submodule update --init --recursive
docker build -f dev-bot/Dockerfile.runner -t my-bot-instance:local .
```
