# Red Hat registry access through the team proxy

Use this only for OCP and ACM customer images under `registry.redhat.io`.
The team-configured proxy provides registry access on port 8451. Registry credentials
stay only in the proxy container; the agent only configures its image client
to use that endpoint.
Other registries, including Quay, Docker Hub, and `registry.access.redhat.com`,
keep their existing routes and workflows.

Do not run `podman login`, request/read Vault credentials, or write real registry
credentials into the bot's auth files. The bot needs no registry username or
token. With remote Podman, the same mapping must exist on the Podman service
host. If the mapping, proxy, or registry access is unavailable, report the
blocker. Do not bypass the proxy or interpret a failed scan as NOT AFFECTED.

## Configure access when needed

Before a Red Hat image pull, derive the team proxy hostname from the existing
`EXECUTOR_ADDR` (`<Service>:9090`). Podman and Buildah read their user
configuration from `~/.config/containers`, so the drop-ins below can be
installed there for them. **Skopeo does not read `$XDG_CONFIG_HOME`,
`~/.config/containers`, or user-level drop-ins; it only reads
`/etc/containers/...` or the explicit `--registries-conf` and `--registries.d`
flags.** For Skopeo, build a task-local configuration directory and pass both
flags on every Skopeo invocation:

```bash
set -euo pipefail
scratch=<task scratch directory, e.g. /tmp/opencode/CCXDEV-12345>
proxy_host=${EXECUTOR_ADDR:?Executor Service address is required}
proxy_host=${proxy_host%:*}
mkdir -p "$scratch/registries.conf.d" "$scratch/registries.d"

# Keep the existing signature policy and all other registries'
# signature-store settings.
cp /etc/containers/registries.d/*.yaml "$scratch/registries.d/" 2>/dev/null || true

cat > "$scratch/registries.conf.d/90-rehor-redhat.conf" <<EOF
[[registry]]
prefix = "registry.redhat.io"
location = "${proxy_host}:8451"
insecure = true
EOF

# Signature discovery follows the remapped hostname, so the mirror host
# needs its own sigstore mapping back to the public Red Hat store.
cat > "$scratch/registries.d/team-redhat-proxy.yaml" <<EOF
docker:
  "${proxy_host}:8451":
    sigstore: https://registry.redhat.io/containers/sigstore
EOF

# Every Skopeo call that must use the proxy carries both flags:
skopeo --registries-conf "$scratch/registries.conf.d/90-rehor-redhat.conf" \
  --registries.d "$scratch/registries.d" inspect --no-tags \
  --override-os linux --override-arch amd64 \
  docker://registry.redhat.io/<namespace>/<repo>:<tag>
```

For Podman and Buildah only, the same two files may instead be written to
`~/.config/containers/registries.conf.d/` and
`~/.config/containers/registries.d/` (copying the existing system
`registries.d` files first, because a user signature directory overrides the
system directory); those clients read user-level configuration and need no
flags.

This is client configuration, not another proxy. The agent performs this step
from the skill; no startup script is installed. Keep other registry settings
intact. The mapping applies only to `registry.redhat.io`, preserving original
image names in commands and Dockerfile `FROM` lines. `insecure = true` permits
HTTP to the internal Service; the team proxy verifies HTTPS upstream. The
deployment's `NO_PROXY` already includes that Service. The signature mapping
keeps Red Hat signature verification working after remapping: signatures come
from the public HTTPS signature store through the existing outbound proxy,
without registry credentials. Without the `--registries.d` mapping for the
mirror host, Skopeo falls back to the empty built-in signature store and every
`copy` fails with `A signature was required, but no signature exists` even
though the image is signed. Do not drop `--remove-signatures` or otherwise
bypass the signature policy. Remove the task-local configuration directory when
routing is no longer needed.

## Inspect and scan a published image

Syft's direct registry source does not use the containers/image mapping.
Download through Skopeo, then scan the local OCI layout. Set `image` to the
affected customer-published tag from the applicable catalog reference:

```bash
set -euo pipefail
# Example: replace latest with the affected published tag when known.
image=registry.redhat.io/lightspeed-services-ocp/ocp-rules-rhel9:latest
conf="$scratch/registries.conf.d/90-rehor-redhat.conf"
sigdir="$scratch/registries.d"
digest=$(skopeo --registries-conf "$conf" --registries.d "$sigdir" inspect \
  --no-tags --override-os linux --override-arch amd64 \
  --format '{{.Digest}}' "docker://${image}")
image_repo=${image%:*}
image_dir=$(mktemp -d)
skopeo --registries-conf "$conf" --registries.d "$sigdir" copy \
  --remove-signatures --override-os linux --override-arch amd64 \
  "docker://${image_repo}@${digest}" "oci:${image_dir}/image:scan"
syft "oci-dir:${image_dir}/image" -o json > "${image_dir}/sbom.json"
printf 'Image: %s\nSource digest: %s\nPlatform: linux/amd64\nSBOM: %s\n' \
  "$image" "$digest" "${image_dir}/sbom.json"
```

Use `--no-tags` for inspections: Skopeo's separate repository-tag listing can
bypass registry remapping. This workflow needs only the selected image.

Record the original tag, resolved source digest, platform, and package evidence.
The copy is pinned to that digest so a moving tag cannot change the scan input.
The source signature policy is still enforced. `--remove-signatures` only omits
signatures from the OCI destination, which cannot store these Docker signatures.
OCI conversion may change the local manifest digest; keep the source digest as
the registry evidence. Inspect `sbom.json` and retain the evidence before
removing the temporary image directory. Use the ACM repository from
`insights-on-prem-acm.md` for ACM tickets. Direct Syft registry scans remain
appropriate for the existing Quay workflow.

**Disk hygiene:** delete the OCI layout and any Grype DB tarball after each
scan — the evidence is the SBOM, digests, and command logs, not the image
bytes. The pod's 8Gi ephemeral-storage limit evicts the pod (destroying all
work) when copies accumulate across cycles. Do not re-copy an image whose
digest is already recorded; re-run Syft/Grype against the stored SBOM instead.
Reuse one OCI layout per digest per cycle at most.

## Local image builds

The runner's `container-scan` preset installs Buildah. Podman and Docker may be
absent. Use `buildah bud -t localhost/cve-audit -f <Containerfile> .` when needed,
then export the built image for Syft/Grype instead of assuming a Docker daemon:

```bash
image_dir=$(mktemp -d)
buildah push localhost/cve-audit "oci:${image_dir}/image:scan"
syft "oci-dir:${image_dir}/image" -o json > "${image_dir}/sbom.json"
grype "oci-dir:${image_dir}/image" --fail-on medium --only-fixed
```

Retain the evidence before removing the temporary layout and local build image.
If the deployed pod cannot run Buildah or a nested UBI container, report that
local-build blocker and use the required Konflux build checks. Published-image
verification through Skopeo/Syft still runs without a container runtime; failure
to perform it keeps the production gate open.

## RPM lockfile helpers in a nested UBI container

The ACM helper requires `scripts/.dockerconfig.json` to exist and passes it to
Skopeo as `REGISTRY_AUTH_FILE`. When that file is absent, create a credential-free
file containing `{"auths":{}}` for the helper; do not commit it or overwrite an
existing file. Remove the temporary file after the helper finishes.

Run the helper in the required Linux/amd64 UBI9 environment, forwarding the
registry mapping into that environment. Add this mount to its container command:

```bash
-v "$scratch/registries.conf.d:/etc/containers/registries.conf.d:ro"
-v "$scratch/registries.d:/etc/containers/registries.d:ro"
```

Also forward the existing `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` and lowercase
equivalents using `-e NAME`. The nested container must resolve and reach the
team proxy Service; use the pod's network (`--network=host`) where supported.
Verify `skopeo inspect --no-tags` for the base image inside that container before running
the helper; inside the nested container the mounted `/etc/containers` files are
read without flags, but pass `--registries-conf`/`--registries.d` when invoking
Skopeo outside it. Lightwell authentication is
separate and remains required by the Python dependency workflow.

References: [Rehor proxy guide](https://github.com/OpenShift-Fleet/rehor/blob/master/docs/redhat-registry-proxy.md),
[containers/image registry configuration](https://github.com/containers/image/blob/main/docs/containers-registries.conf.5.md),
[registry drop-ins](https://github.com/containers/image/blob/main/docs/containers-registries.conf.d.5.md),
[signature-store configuration](https://github.com/containers/image/blob/main/docs/containers-registries.d.5.md),
[Syft scan targets](https://oss.anchore.com/docs/guides/sbom/scan-targets/).
