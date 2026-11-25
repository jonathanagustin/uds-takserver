#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Desktop prototype, package stage: turn the image of stages/image.sh into a
# Zarf package and a UDS bundle, and check both as far as that is possible
# without a cluster. The work is done by ../zarf/*, ../chart and ../bundle/*;
# this script is the record of the exact commands. Nothing is deployed,
# nothing is pushed, and the only cluster access is `kubectl get crd`.
#
#   tools         versions of the tools in use
#   lint          yamllint (repository config), shellcheck (hk.pkl flags),
#                 codespell and the licence header check on the package files
#   stage         ../zarf/create-package.sh TAG --stage-only: the tree Zarf
#                 builds from, with the tag, image source and attachments
#   zarf-lint     `zarf dev lint` on the staged tree and on ../zarf
#   helm          `helm lint --strict` and `helm template` of ../chart, with
#                 the default values and with every optional branch on
#   render        `zarf dev inspect manifests`: what the package would apply
#   crds          read the Package and TakClient CRDs from the cluster
#                 (kubectl get, nothing else) and turn them into JSON Schemas
#   validate      kubeconform: every rendered object against the Kubernetes
#                 schemas and those two CRD schemas, unknown fields rejected
#   test          ../zarf/test_package.py: structure, pod security, network
#                 rules, the Secret projection, the UDS Core policies
#   create        `zarf package create` of the staged tree into pkg/out, with
#                 the image read from the local Docker image store
#   inspect       `zarf package inspect` definition, images, SBOM, attachments
#   upstream      resolve the four upstream package references of the bundle
#                 anonymously (definition only, no layers)
#   bundle        ../bundle/create-bundle.sh --packages takx-desktop, then
#                 `uds inspect`. The full bundle is staged and checked against
#                 the uds schema but not created: see `bundle-full`
#   bundle-full   create the whole bundle. Pulls about 2.7 GB and needs about
#                 12 GB of free disk; refuses below TAKX_BUNDLE_MIN_FREE_GIB
#   all           everything above except bundle-full, in order
#
# Layout (TAKX_PROTO_ROOT, default /var/lib/takx-proto):
#   pkg/status/image.json     written by stages/image.sh; names the image tag
#   pkg/package/stage/        the staged package tree
#   pkg/package/render/       rendered manifests
#   pkg/package/crds/         CRDs and their JSON Schemas
#   pkg/package/evidence/     one transcript per step
#   pkg/out/                  zarf-package-takx-desktop-amd64-TAG.tar.zst and
#                             the bundle
#   pkg/status/package.json   result of the last `create`, `inspect`, `bundle`
#
# `create` and `bundle` run uds in a container without network, limited to
# TAKX_PACKAGE_CPUS (2) and TAKX_PACKAGE_MEMORY (3g), because this sandbox
# shares its node with a running cluster. The command inside is the one
# create-package.sh and create-bundle.sh run themselves; outside a shared
# machine, call those two scripts directly.
#
# Environment: UDS (default: the pinned copy under server/tools, else `uds`),
# KUBECONFORM (default `kubeconform`), TAKX_KUBE_VERSION (schema version for
# kubeconform, default 1.36.3, the version of the sandbox cluster),
# TAKX_PACKAGE_RUNNER_IMAGE (default mirror.gcr.io/library/debian:trixie-slim).
#
# What is inside the image: TAKX 5.7 (0b59a9bb) built from public sources on
# Kernel 8.30.5 + project patches, CommonCommo 1.17.2 patched and the NASA
# WorldWind interop build. It is not the official TAKX 5.7 distribution.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
proto="$(cd "${here}/.." && pwd)"
repo="$(cd "${proto}/../.." && pwd)"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
work="${root}/pkg/package"
out="${root}/pkg/out"
evidence="${work}/evidence"
image_repo=ghcr.io/jonathanagustin/uds-takserver/takx-desktop
kube_version="${TAKX_KUBE_VERSION:-1.36.3}"
kubeconform="${KUBECONFORM:-kubeconform}"
runner_image="${TAKX_PACKAGE_RUNNER_IMAGE:-mirror.gcr.io/library/debian:trixie-slim}"

pinned="${root}/server/tools/uds-0.42.0/uds"
if [[ -z "${UDS:-}" ]]; then
  if [[ -x "${pinned}" ]]; then UDS="${pinned}"; else UDS="$(command -v uds)"; fi
fi
export UDS
# uds reads ./uds-config.yaml of the directory it runs in; none of these
# steps wants that, so every uds call runs from the work directory.
mkdir -p "${work}" "${evidence}" "${out}"
uds() { (cd "${work}" && "${UDS}" "$@"); }

# Files this stage owns, for the linters.
package_files() {
  (cd "${repo}" && find takx/desktop-prototype/zarf takx/desktop-prototype/chart takx/desktop-prototype/bundle \
    takx/desktop-prototype/stages/package.sh -type f | LC_ALL=C sort)
}

image_tag() {
  python3 -I -c 'import json, sys
tags = json.load(open(sys.argv[1], encoding="utf-8"))["tags"]
match = [tag for tag in tags if tag.startswith(sys.argv[2] + ":")]
print(match[0].split(":", 1)[1])' "${root}/pkg/status/image.json" "${image_repo}"
}

free_gib() { df --output=avail -BG "${root}" | tail -1 | tr -dc '0-9'; }

write_status() { # step status notes
  python3 -I - "${root}/pkg/status/package.json" "$@" <<'PY'
import datetime
import json
import os
import sys
path, step, status, notes = sys.argv[1:5]
data = {}
if os.path.exists(path):
    with open(path, encoding="utf-8") as stream:
        data = json.load(stream)
data.setdefault("stage", "package")
data.setdefault("steps", {})[step] = {
    "status": status,
    "time": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "notes": notes,
}
with open(path + ".tmp", "w", encoding="utf-8") as stream:
    json.dump(data, stream, indent=1)
    stream.write("\n")
os.replace(path + ".tmp", path)
PY
}

# Run uds in a container: no network, bounded CPU and memory, the Docker
# socket for `source: daemon`, and only the package work directories.
bounded_uds() { # name args...
  local name="$1"
  shift
  mkdir -p "${work}/tmp" "${work}/home"
  docker run --rm --name "takxpkg-${name}" --network none \
    --cpus "${TAKX_PACKAGE_CPUS:-2}" --memory "${TAKX_PACKAGE_MEMORY:-3g}" \
    -e HOME="${work}/home" -e TMPDIR="${work}/tmp" -e GOMAXPROCS="${TAKX_PACKAGE_CPUS:-2}" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "${UDS}:/usr/local/bin/uds:ro" \
    -v "${work}:${work}" -v "${out}:${out}" -v "${repo}/bundle:${repo}/bundle:ro" \
    -w "${work}" "${runner_image}" uds "$@"
}

do_tools() {
  {
    echo "uds $("${UDS}" version) at ${UDS}"
    echo "zarf $(uds zarf version)"
    echo "helm $(uds zarf tools helm version --short 2>/dev/null || uds zarf tools helm version)"
    echo "kubeconform $("${kubeconform}" -v 2>&1)"
    echo "yamllint $(yamllint --version 2>&1)"
    echo "shellcheck $(shellcheck --version | sed -n 's/^version: //p')"
    echo "image tag $(image_tag)"
    echo "free disk $(free_gib) GiB"
  } | tee "${evidence}/tools.txt"
}

do_lint() {
  local files=() shell=() file rc=0
  mapfile -t files < <(package_files)
  for file in "${files[@]}"; do
    case "${file}" in *.sh) shell+=("${file}");; *) ;; esac
  done
  {
    echo "== yamllint --strict -c .yamllint (${#files[@]} files offered; yamllint picks the YAML ones)"
    (cd "${repo}" && yamllint --strict -c .yamllint takx/desktop-prototype/zarf takx/desktop-prototype/chart \
      takx/desktop-prototype/bundle) || rc=1
    echo "== shellcheck --enable=all --severity=style --exclude=SC2310,SC2312 (${#shell[@]} scripts)"
    (cd "${repo}" && shellcheck --enable=all --severity=style --exclude=SC2310,SC2312 "${shell[@]}") || rc=1
    echo "== shellcheck of the action commands in zarf/components/*.yaml (sh or the shell they name)"
    while IFS= read -r file; do
      (cd "${repo}" && python3 -I takx/desktop-prototype/zarf/test_package.py --action-scripts "${file}" "${work}/actions") || rc=1
    done < <(cd "${repo}" && find takx/desktop-prototype/zarf/components -name '*.yaml' | LC_ALL=C sort)
    if compgen -G "${work}/actions/*" >/dev/null; then
      shellcheck --severity=style "${work}"/actions/* || rc=1
    fi
    echo "== codespell (repository .codespellrc)"
    if command -v codespell >/dev/null; then
      (cd "${repo}" && codespell "${files[@]}") || rc=1
    elif command -v uvx >/dev/null; then
      (cd "${repo}" && uvx --quiet codespell==2.4.3 "${files[@]}") || rc=1
    else
      echo "codespell: not installed, not run"
      rc=1
    fi
    echo "== licence header (.license_config.yaml: GPL-3.0-only, uds-takserver authors)"
    (cd "${repo}" && python3 -I takx/desktop-prototype/zarf/test_package.py --headers "${files[@]}") || rc=1
    echo "lint exit ${rc}"
  } 2>&1 | tee "${evidence}/lint.txt"
  [[ "$(tail -1 "${evidence}/lint.txt")" == "lint exit 0" ]]
}

do_stage() {
  rm -rf "${work}/stage"
  "${proto}/zarf/create-package.sh" "$(image_tag)" --stage-only "${work}/stage" | tee "${evidence}/stage.txt"
  (cd "${work}/stage" && find . -type f | LC_ALL=C sort | xargs sha256sum) >>"${evidence}/stage.txt"
}

do_zarf_lint() {
  local rc=0
  {
    echo "== uds zarf dev lint ${work}/stage/zarf"
    uds zarf dev lint "${work}/stage/zarf" --no-color || rc=1
    echo "== uds zarf dev lint ${proto}/zarf (as tracked, with the placeholder tag)"
    uds zarf dev lint "${proto}/zarf" --no-color || rc=1
    echo "zarf-lint exit ${rc}"
  } 2>&1 | tee "${evidence}/zarf-lint.txt"
  [[ "$(tail -1 "${evidence}/zarf-lint.txt")" == "zarf-lint exit 0" ]]
}

# Every optional branch of the chart at once.
write_all_values() {
  cat >"${work}/render/values-all.yaml" <<'YAML'
host: takx-two
domain: example.test
takx:
  args: "-create"
identity:
  expectHost: takserver-stream.example.test
  connectHost: tak.example.test
tak:
  gateway:
    ip: 10.43.0.99
  egress:
    streaming: true
    api: true
  hostAliases:
    - ip: 10.43.0.98
      hostnames: [extra.example.test]
egress:
  hosts: [tiles.example.test]
persistence:
  enabled: true
  storageClass: local-path
YAML
}

do_helm() {
  local rc=0
  mkdir -p "${work}/render"
  write_all_values
  {
    echo "== helm lint --strict ${proto}/chart"
    uds zarf tools helm lint --strict "${proto}/chart" || rc=1
    echo "== helm lint --strict with every optional branch"
    uds zarf tools helm lint --strict "${proto}/chart" -f "${work}/render/values-all.yaml" || rc=1
    echo "== helm template (default values)"
    uds zarf tools helm template takx-desktop "${proto}/chart" -n takx-desktop \
      --kube-version "${kube_version}" >"${work}/render/helm-default.yaml" || rc=1
    echo "== helm template (every optional branch)"
    uds zarf tools helm template takx-desktop "${proto}/chart" -n takx-desktop \
      --kube-version "${kube_version}" -f "${work}/render/values-all.yaml" >"${work}/render/helm-all.yaml" || rc=1
    echo "== helm template must refuse values outside the schema"
    if uds zarf tools helm template takx-desktop "${proto}/chart" --set sso.groups=null >/dev/null 2>"${work}/render/helm-bad.err"; then
      echo "accepted sso.groups=null"
      rc=1
    else
      head -3 "${work}/render/helm-bad.err"
    fi
    if uds zarf tools helm template takx-desktop "${proto}/chart" --set nope=1 >/dev/null 2>"${work}/render/helm-bad.err"; then
      echo "accepted an unknown key"
      rc=1
    else
      head -3 "${work}/render/helm-bad.err"
    fi
    grep -c '^kind:' "${work}/render/helm-default.yaml" "${work}/render/helm-all.yaml"
    echo "helm exit ${rc}"
  } 2>&1 | tee "${evidence}/helm.txt"
  [[ "$(tail -1 "${evidence}/helm.txt")" == "helm exit 0" ]]
}

do_render() {
  local rc=0
  mkdir -p "${work}/render"
  {
    echo "== uds zarf dev inspect manifests ${work}/stage/zarf (KUBECONFIG=/dev/null: no cluster)"
    KUBECONFIG=/dev/null uds zarf dev inspect manifests "${work}/stage/zarf" --no-color \
      --kube-version "${kube_version}" >"${work}/render/zarf-default.yaml" || rc=1
    echo "== the same with package values set"
    KUBECONFIG=/dev/null uds zarf dev inspect manifests "${work}/stage/zarf" --no-color \
      --kube-version "${kube_version}" \
      --set-values global.domain=example.test --set-values identity.name=takx-desktop \
      --set-values identity.callsign=takx-desktop --set-values desktop.host=takx-two \
      --set-values desktop.image=example.invalid/other:tag \
      >"${work}/render/zarf-set.yaml" || rc=1
    grep -c '^kind:' "${work}/render/zarf-default.yaml" "${work}/render/zarf-set.yaml"
    echo "render exit ${rc}"
  } 2>&1 | tee "${evidence}/render.txt"
  [[ "$(tail -1 "${evidence}/render.txt")" == "render exit 0" ]]
}

do_crds() {
  local name
  mkdir -p "${work}/crds/schemas"
  for name in packages.uds.dev takclients.tak.uds.dev; do
    kubectl get crd "${name}" -o json >"${work}/crds/${name}.json"
  done
  python3 -I "${proto}/zarf/test_package.py" --crd-schemas "${work}/crds/schemas" \
    "${work}/crds/packages.uds.dev.json" "${work}/crds/takclients.tak.uds.dev.json" | tee "${evidence}/crds.txt"
  kubectl version -o json | python3 -I -c 'import json, sys
print("cluster", json.load(sys.stdin)["serverVersion"]["gitVersion"])' | tee -a "${evidence}/crds.txt"
}

do_validate() {
  local rc=0 file
  mkdir -p "${work}/schemas"
  {
    for file in helm-default helm-all zarf-default zarf-set; do
      echo "== kubeconform -strict -kubernetes-version ${kube_version} ${file}.yaml"
      "${kubeconform}" -strict -summary -verbose -kubernetes-version "${kube_version}" -cache "${work}/schemas" \
        -schema-location default \
        -schema-location "${work}/crds/schemas/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json" \
        "${work}/render/${file}.yaml" || rc=1
    done
    echo "== a wrong field must be rejected (control)"
    sed -e 's/^    serviceMesh:/    serviceMash:/' "${work}/render/helm-default.yaml" >"${work}/render/control-bad.yaml"
    if "${kubeconform}" -strict -kubernetes-version "${kube_version}" -cache "${work}/schemas" \
      -schema-location default \
      -schema-location "${work}/crds/schemas/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json" \
      "${work}/render/control-bad.yaml"; then
      echo "control: the misspelt field was accepted"
      rc=1
    fi
    echo "validate exit ${rc}"
  } 2>&1 | tee "${evidence}/validate.txt"
  [[ "$(tail -1 "${evidence}/validate.txt")" == "validate exit 0" ]]
}

do_test() {
  (cd "${repo}" && TAKX_PACKAGE_WORK="${work}" python3 -B -m unittest -v takx/desktop-prototype/zarf/test_package.py) 2>&1 |
    tee "${evidence}/test.txt"
  grep -q '^OK' "${evidence}/test.txt"
}

do_create() {
  local tag start seconds archive
  tag="$(image_tag)"
  archive="${out}/zarf-package-takx-desktop-amd64-${tag}.tar.zst"
  [[ -s "${work}/stage/zarf/zarf.yaml" ]] || do_stage
  if (($(free_gib) < 8)); then
    echo "package: $(free_gib) GiB free; the create needs about 6 GiB while it runs" >&2
    exit 1
  fi
  rm -f "${archive}"
  start="${SECONDS}"
  # The command create-package.sh ends with, inside the bounded container.
  bounded_uds create zarf package create "${work}/stage/zarf" --confirm --no-color -o "${out}" \
    --cache "${work}/zarf-cache" 2>&1 | tee "${evidence}/create.txt"
  seconds=$((SECONDS - start))
  [[ -s "${archive}" ]]
  rm -rf "${work}/tmp" "${work}/zarf-cache"
  write_status create ok "$(basename "${archive}") $(stat -c %s "${archive}") bytes sha256 $(sha256sum "${archive}" | cut -d' ' -f1) in ${seconds} s; image from the local Docker store; no network"
}

do_inspect() {
  local tag archive rc=0
  tag="$(image_tag)"
  archive="${out}/zarf-package-takx-desktop-amd64-${tag}.tar.zst"
  rm -rf "${work}/inspect"
  mkdir -p "${work}/inspect/sbom" "${work}/inspect/attachments"
  {
    echo "== archive"
    ls -l "${archive}"
    sha256sum "${archive}"
    echo "== uds zarf package inspect definition"
    uds zarf package inspect definition "${archive}" --no-color || rc=1
    echo "== uds zarf package inspect images"
    uds zarf package inspect images "${archive}" --no-color || rc=1
    echo "== uds zarf package inspect sbom"
    uds zarf package inspect sbom "${archive}" --output "${work}/inspect/sbom" --no-color || rc=1
    find "${work}/inspect/sbom" -type f -printf '%s %P\n' | LC_ALL=C sort -k2
    echo "== uds zarf package inspect documentation"
    uds zarf package inspect documentation "${archive}" --output "${work}/inspect/attachments" --no-color || rc=1
    find "${work}/inspect/attachments" -type f -printf '%s %P\n' | LC_ALL=C sort -k2
    echo "inspect exit ${rc}"
  } 2>&1 | tee "${evidence}/inspect.txt"
  [[ "$(tail -1 "${evidence}/inspect.txt")" == "inspect exit 0" ]]
  (cd "${repo}" && TAKX_PACKAGE_WORK="${work}" python3 -I takx/desktop-prototype/zarf/test_package.py \
    --built "${work}/inspect" "${tag}") | tee "${evidence}/inspect-check.txt"
  write_status inspect ok "definition, images, SBOM and attachments read back from $(basename "${archive}")"
}

# Resolve every upstream package of the bundle without its layers: the
# definition only (tens of kilobytes), anonymously, with the signature checked
# against the identity the bundle itself names. Then compare what the bundle
# asks of each package (optional components, the components and charts it
# overrides) with what the package has.
do_upstream() {
  local bundle="${proto}/bundle/uds-bundle.yaml" rc=0 count index name repository ref regexp issuer file wanted have
  count="$(uds zarf tools yq '.packages | length' "${bundle}")"
  {
    for ((index = 0; index < count; index++)); do
      repository="$(uds zarf tools yq ".packages[${index}].repository // \"\"" "${bundle}")"
      [[ -n "${repository}" ]] || continue
      name="$(uds zarf tools yq ".packages[${index}].name" "${bundle}")"
      ref="$(uds zarf tools yq ".packages[${index}].ref" "${bundle}")"
      regexp="$(uds zarf tools yq ".packages[${index}].keylessVerification.certificateIdentityRegexp" "${bundle}")"
      issuer="$(uds zarf tools yq ".packages[${index}].keylessVerification.certificateOIDCIssuer" "${bundle}")"
      file="${work}/upstream-${name}.yaml"
      echo "== ${name}: uds zarf package inspect definition oci://${repository}:${ref} --verify=always (anonymous, definition only)"
      if ! HOME="${work}/home-upstream" DOCKER_CONFIG="${work}/home-upstream/no-docker-config" \
        "${UDS}" zarf package inspect definition "oci://${repository}:${ref}" --no-color --verify=always \
        --certificate-identity-regexp "${regexp}" --certificate-oidc-issuer "${issuer}" \
        >"${file}" 2>"${file}.err"; then
        rc=1
      fi
      grep -E 'pulling package|Verified OK|ERR' "${file}.err" | sed -e 's/^[0-9: -]* //' || true
      grep -q '^Verified OK' "${file}.err" || { echo "signature not verified"; rc=1; }
      uds zarf tools yq '"package " + .metadata.name + " " + .metadata.version + " (" + .metadata.architecture + "): components " + ([.components[].name] | join(" "))' "${file}" || rc=1
      while IFS= read -r wanted; do
        have="$(COMPONENT="${wanted}" uds zarf tools yq '[.components[] | select(.name == strenv(COMPONENT))] | length' "${file}")"
        echo "  optional component ${wanted}: $([[ "${have}" == 1 ]] && echo present || echo MISSING)"
        [[ "${have}" == 1 ]] || rc=1
      done < <(uds zarf tools yq ".packages[${index}].optionalComponents[]" "${bundle}")
      while IFS= read -r wanted; do
        have="$(COMPONENT="${wanted%%/*}" CHART="${wanted##*/}" uds zarf tools yq \
          '[.components[] | select(.name == strenv(COMPONENT)) | .charts[] | select(.name == strenv(CHART))] | length' "${file}")"
        echo "  override ${wanted}: $([[ "${have}" == 1 ]] && echo "component and chart present" || echo MISSING)"
        [[ "${have}" == 1 ]] || rc=1
      done < <(uds zarf tools yq ".packages[${index}].overrides // {} | to_entries[] | .key as \$component | .value | keys[] | \$component + \"/\" + ." "${bundle}")
    done
    echo "upstream exit ${rc}"
  } 2>&1 | tee "${evidence}/upstream.txt"
  [[ "$(tail -1 "${evidence}/upstream.txt")" == "upstream exit 0" ]]
}

do_bundle() {
  local tag rc=0 bundle
  tag="$(image_tag)"
  bundle="${out}/uds-bundle-takx-desktop-proto-partial-amd64-${tag}.tar.zst"
  rm -rf "${work}/bundle-full" "${work}/bundle-one" "${bundle}"
  {
    echo "== stage the whole bundle and the takx-desktop-only bundle"
    "${proto}/bundle/create-bundle.sh" "${out}" "${tag}" --stage-only "${work}/bundle-full" || rc=1
    "${proto}/bundle/create-bundle.sh" "${out}" "${tag}" --packages takx-desktop --stage-only "${work}/bundle-one" || rc=1
    echo "== both staged definitions and the tracked one against uds.schema.json of uds v0.42.0"
    if [[ ! -s "${work}/uds.schema.json" ]]; then
      curl -fsSL -o "${work}/uds.schema.json" \
        https://raw.githubusercontent.com/defenseunicorns/uds-cli/v0.42.0/uds.schema.json || rc=1
    fi
    sha256sum "${work}/uds.schema.json"
    (cd "${repo}" && python3 -I takx/desktop-prototype/zarf/test_package.py --bundle-schema "${work}/uds.schema.json" \
      "${work}/bundle-full/uds-bundle.yaml" "${work}/bundle-one/uds-bundle.yaml" \
      takx/desktop-prototype/bundle/uds-bundle.yaml) || rc=1
    echo "== uds create (takx-desktop only; no registry, no network)"
    bounded_uds bundle create "${work}/bundle-one" --confirm --no-color --no-progress -o "${out}" \
      --uds-cache "${work}/uds-cache" || rc=1
    rm -rf "${work}/tmp" "${work}/uds-cache"
    ls -l "${bundle}" || rc=1
    echo "== uds inspect"
    uds inspect "${bundle}" --no-color --no-progress || rc=1
    echo "== uds inspect --list-variables"
    uds inspect "${bundle}" --list-variables --no-color --no-progress || rc=1
    echo "bundle exit ${rc}"
  } 2>&1 | tee "${evidence}/bundle.txt"
  [[ "$(tail -1 "${evidence}/bundle.txt")" == "bundle exit 0" ]]
  write_status bundle ok "$(basename "${bundle}") $(stat -c %s "${bundle}") bytes: the takx-desktop package only. The whole bundle was staged and schema-checked, not created"
}

do_bundle_full() {
  local tag need="${TAKX_BUNDLE_MIN_FREE_GIB:-16}"
  tag="$(image_tag)"
  if (($(free_gib) < need)); then
    echo "package: $(free_gib) GiB free, ${need} GiB wanted for the whole bundle (TAKX_BUNDLE_MIN_FREE_GIB); not started" >&2
    exit 1
  fi
  UDS="${UDS}" "${proto}/bundle/create-bundle.sh" "${out}" "${tag}" --confirm --no-color --no-progress -o "${out}" 2>&1 |
    tee "${evidence}/bundle-full.txt"
  write_status bundle-full ok "whole bundle created in ${out}"
}

case "${1:-}" in
  tools) do_tools;;
  lint) do_lint;;
  stage) do_stage;;
  zarf-lint) do_zarf_lint;;
  helm) do_helm;;
  render) do_render;;
  crds) do_crds;;
  validate) do_validate;;
  test) do_test;;
  create) do_create;;
  inspect) do_inspect;;
  upstream) do_upstream;;
  bundle) do_bundle;;
  bundle-full) do_bundle_full;;
  all)
    do_tools; do_lint; do_stage; do_zarf_lint; do_helm; do_render; do_crds; do_validate; do_test
    do_create; do_inspect; do_upstream; do_bundle;;
  -h|--help|"") sed -n '5,56p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//';;
  *) echo "package.sh: unknown step '$1'" >&2; exit 2;;
esac
