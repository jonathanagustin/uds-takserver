#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Checks of the takx-desktop chart, Zarf package and UDS bundle without a cluster.

As a test module (python3 -m unittest takx/desktop-prototype/zarf/test_package.py)
it reads the tracked files, renders the chart with the Helm that the uds CLI
vendors, and asserts what the package would apply:

  - the composition of zarf.yaml and its component files;
  - the pod: users, read-only roots, dropped capabilities, volumes, probes,
    and that the identity Secret is mounted in the init container only;
  - the UDS Package: sign-in, the one exposed port, and the exact egress set;
  - each UDS Core v1.16.0 admission policy (src/pepr/policies) and this
    repository's pod policy (pepr/capabilities/tak-policy.ts), written out
    here as a predicate and applied to the rendered pod, with a control that
    shows each predicate does reject a violating pod;
  - the Secret projection of the identity component, run with the real yq
    expression on a synthetic Secret;
  - the bundle: names, paths and optional components that must exist.

Nothing here contacts a cluster, and passing is not a deploy: admission by
the live UDS Core and takserver-oidc webhooks, the operator's reconcile and
the network policies are only proven by a deploy.

The uds binary comes from $UDS, then the copy pinned by server/15-uds-cli.sh,
then PATH. Tests that need it are skipped when there is none.

As a script it has helpers for stages/package.sh:

  --headers FILE...                licence header of each file
  --action-scripts FILE OUTDIR     write each action `cmd` of a component file
                                   as a shell script, for shellcheck
  --crd-schemas OUTDIR CRD.json... CRDs (kubectl get crd -o json) to strict
                                   JSON Schemas for kubeconform
  --bundle-schema SCHEMA FILE...   uds-bundle.yaml files against uds.schema.json
  --built INSPECT_DIR TAG          what `zarf package inspect` extracted
  --archive DIR IMAGE_ID           the decompressed package: its zarf.yaml
                                   documents and the image in its OCI layout
                                   against the image id of the build
"""
import copy
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PROTO = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(PROTO))
CHART = os.path.join(PROTO, "chart")
BUNDLE = os.path.join(PROTO, "bundle")
PINNED_UDS = "/var/lib/takx-proto/server/tools/uds-0.42.0/uds"
IMAGE = "ghcr.io/jonathanagustin/uds-takserver/takx-desktop"
SELECTOR = {"app.kubernetes.io/name": "takx-desktop"}
GATEWAY = {"remoteNamespace": "istio-passthrough-gateway", "remoteSelector": {"app": "passthrough-ingressgateway"}}

# Every optional branch of the chart at once.
ALL_VALUES = {
    "host": "takx-two",
    "domain": "example.test",
    "takx": {"args": "-create"},
    "identity": {"expectHost": "takserver-stream.example.test", "connectHost": "tak.example.test"},
    "tak": {
        "gateway": {"ip": "10.43.0.99"},
        "egress": {"streaming": True, "api": True},
        "hostAliases": [{"ip": "10.43.0.98", "hostnames": ["extra.example.test"]}],
    },
    "egress": {"hosts": ["tiles.example.test"]},
    "persistence": {"enabled": True, "storageClass": "local-path"},
}


def find_uds():
    """Path of the uds binary, or None."""
    for candidate in (os.environ.get("UDS"), PINNED_UDS, shutil.which("uds")):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


UDS = find_uds()


def run_uds(*arguments, data=None, check=True):
    """Run uds from a neutral directory (it reads ./uds-config.yaml) and return stdout."""
    done = subprocess.run((UDS,) + arguments, input=data, capture_output=True, check=False, cwd="/",
                          env={**os.environ, "KUBECONFIG": "/dev/null"})
    if check and done.returncode != 0:
        raise AssertionError(f"uds {' '.join(arguments[:4])} failed: {done.stderr.decode('utf-8', 'replace')[-600:]}")
    return done.stdout.decode("utf-8")


def load_yaml(path):
    """All documents of a YAML file, through the yq that uds vendors."""
    out = run_uds("zarf", "tools", "yq", "eval-all", "-o", "json", "[.]", os.path.abspath(path))
    return [doc for doc in json.loads(out) if doc]


def load_yaml_text(text):
    """All documents of YAML text."""
    out = run_uds("zarf", "tools", "yq", "eval-all", "-o", "json", "[.]", "-", data=text.encode())
    return [doc for doc in json.loads(out) if doc]


def helm_template(values=None, extra=()):
    """Render the chart and return its documents."""
    arguments = ["zarf", "tools", "helm", "template", "takx-desktop", CHART, "-n", "takx-desktop"]
    if values is not None:
        arguments += ["-f", "-"]
    text = run_uds(*arguments, *extra, data=json.dumps(values).encode() if values is not None else None)
    return load_yaml_text(text)


def one(documents, kind):
    """The single document of a kind."""
    found = [doc for doc in documents if doc.get("kind") == kind]
    if len(found) != 1:
        raise AssertionError(f"expected one {kind}, found {len(found)}")
    return found[0]


def all_containers(spec):
    return spec.get("initContainers", []) + spec.get("containers", []) + spec.get("ephemeralContainers", [])


# --- UDS Core v1.16.0 pod and service policies -------------------------------
#
# One predicate per policy of src/pepr/policies/{security,networking,storage,
# istio}.ts at tag v1.16.0 (fd9769cd). Each returns the list of violations the
# Validate callback would deny on; an empty list is an approval. The mutations
# of the same policies (defaults for runAsUser, drop ALL, ...) are not
# modelled: the pod is judged as the chart renders it, which is stricter.


def _contexts(pod):
    """securityContextContainers(): containers that set a securityContext."""
    return [(c["name"], c["securityContext"]) for c in all_containers(pod["spec"]) if c.get("securityContext")]


def disallow_privileged(pod):
    return [name for name, ctx in _contexts(pod)
            if ctx.get("allowPrivilegeEscalation", True) or ctx.get("privileged")]


def _is_root(ctx):
    return ctx.get("runAsNonRoot") is False or ctx.get("runAsUser") == 0 or 0 in ctx.get("supplementalGroups", [])


def require_non_root_user(pod):
    found = ["pod"] if _is_root(pod["spec"].get("securityContext") or {}) else []
    return found + [name for name, ctx in _contexts(pod) if _is_root(ctx)]


def restrict_proc_mount(pod):
    return [name for name, ctx in _contexts(pod) if ctx.get("procMount") not in (None, "Default")]


def restrict_seccomp(pod):
    allowed = (None, "RuntimeDefault", "Localhost")
    pod_type = ((pod["spec"].get("securityContext") or {}).get("seccompProfile") or {}).get("type")
    if pod_type not in allowed:
        return ["pod"]
    return [name for name, ctx in _contexts(pod) if (ctx.get("seccompProfile") or {}).get("type") not in allowed]


def disallow_selinux_options(pod):
    def bad(ctx):
        options = ctx.get("seLinuxOptions") or {}
        return bool(options.get("user") or options.get("role"))
    if bad(pod["spec"].get("securityContext") or {}):
        return ["pod"]
    return [name for name, ctx in _contexts(pod) if bad(ctx)]


def restrict_selinux_type(pod):
    allowed = (None, "container_t", "container_init_t", "container_kvm_t")
    if ((pod["spec"].get("securityContext") or {}).get("seLinuxOptions") or {}).get("type") not in allowed:
        return ["pod"]
    return [name for name, ctx in _contexts(pod) if (ctx.get("seLinuxOptions") or {}).get("type") not in allowed]


def drop_all_capabilities(pod):
    return [name for name, ctx in _contexts(pod) if "ALL" not in ((ctx.get("capabilities") or {}).get("drop") or [])]


def restrict_capabilities(pod):
    return [name for name, ctx in _contexts(pod)
            if any(cap != "NET_BIND_SERVICE" for cap in ((ctx.get("capabilities") or {}).get("add") or []))]


def disallow_host_namespaces(pod):
    return [key for key in ("hostNetwork", "hostIPC", "hostPID") if pod["spec"].get(key)]


def restrict_host_ports(pod):
    return [c["name"] for c in all_containers(pod["spec"]) if any(p.get("hostPort") for p in c.get("ports", []))]


UDS_VOLUME_TYPES = ("configMap", "csi", "downwardAPI", "emptyDir", "ephemeral", "image", "persistentVolumeClaim",
                    "projected", "secret")


def restrict_volume_types(pod):
    found = []
    for volume in pod["spec"].get("volumes", []):
        kind = next((key for key in volume if key != "name"), "unknown")
        if kind not in UDS_VOLUME_TYPES:
            found.append(f"{volume['name']}: {kind}")
    return found


def restrict_host_path_write(pod):
    found = []
    for volume in pod["spec"].get("volumes", []):
        if "hostPath" in volume:
            mounts = [m for c in all_containers(pod["spec"]) for m in c.get("volumeMounts", []) if m["name"] == volume["name"]]
            if any(not m.get("readOnly") for m in mounts):
                found.append(volume["name"])
    return found


def restrict_istio_sidecar_overrides(pod):
    blocked = ("sidecar.istio.io/bootstrapOverride", "sidecar.istio.io/discoveryAddress", "sidecar.istio.io/proxyImage",
               "proxy.istio.io/config", "sidecar.istio.io/userVolume", "sidecar.istio.io/userVolumeMount")
    return sorted(key for key in pod["metadata"].get("annotations", {}) if key in blocked)


def restrict_istio_traffic_overrides(pod):
    blocked = ("sidecar.istio.io/inject", "traffic.sidecar.istio.io/excludeInboundPorts",
               "traffic.sidecar.istio.io/excludeInterfaces", "traffic.sidecar.istio.io/excludeOutboundIPRanges",
               "traffic.sidecar.istio.io/excludeOutboundPorts", "traffic.sidecar.istio.io/includeInboundPorts",
               "traffic.sidecar.istio.io/includeOutboundIPRanges", "traffic.sidecar.istio.io/includeOutboundPorts",
               "sidecar.istio.io/interceptionMode", "traffic.sidecar.istio.io/kubevirtInterfaces",
               "istio.io/redirect-virtual-interfaces")
    found = []
    for key, value in pod["metadata"].get("annotations", {}).items():
        if key == "sidecar.istio.io/inject" and str(value).strip() == "true":
            continue
        if key in blocked:
            found.append(f"annotation {key}")
    for key, value in pod["metadata"].get("labels", {}).items():
        if key == "sidecar.istio.io/inject" and str(value).strip() != "true":
            found.append(f"label {key}")
    return sorted(found)


def restrict_istio_ambient_overrides(pod):
    return sorted(key for key in pod["metadata"].get("annotations", {}) if key == "ambient.istio.io/bypass-inbound-capture")


def restrict_istio_user(pod):
    ctx = pod["spec"].get("securityContext") or {}
    found = []
    if 1337 in (ctx.get("runAsUser"), ctx.get("runAsGroup"), ctx.get("fsGroup")) or 1337 in ctx.get("supplementalGroups", []):
        found.append("pod")
    for container in all_containers(pod["spec"]):
        own = container.get("securityContext") or {}
        if 1337 in (own.get("runAsUser"), own.get("runAsGroup")):
            found.append(container["name"])
    return found


UDS_POD_POLICIES = {
    "DisallowPrivileged": disallow_privileged,
    "RequireNonRootUser": require_non_root_user,
    "RestrictProcMount": restrict_proc_mount,
    "RestrictSeccomp": restrict_seccomp,
    "DisallowSELinuxOptions": disallow_selinux_options,
    "RestrictSELinuxType": restrict_selinux_type,
    "DropAllCapabilities": drop_all_capabilities,
    "RestrictCapabilities": restrict_capabilities,
    "DisallowHostNamespaces": disallow_host_namespaces,
    "RestrictHostPorts": restrict_host_ports,
    "RestrictVolumeTypes": restrict_volume_types,
    "RestrictHostPathWrite": restrict_host_path_write,
    "RestrictIstioSidecarOverrides": restrict_istio_sidecar_overrides,
    "RestrictIstioTrafficOverrides": restrict_istio_traffic_overrides,
    "RestrictIstioAmbientOverrides": restrict_istio_ambient_overrides,
    "RestrictIstioUser": restrict_istio_user,
}


def _violate(pod, policy):
    """A copy of the pod changed so that exactly this policy must reject it."""
    bad = copy.deepcopy(pod)
    spec, first = bad["spec"], bad["spec"]["containers"][0]
    ctx = first["securityContext"]
    annotations = bad["metadata"].setdefault("annotations", {})
    changes = {
        "DisallowPrivileged": lambda: ctx.update(allowPrivilegeEscalation=True),
        "RequireNonRootUser": lambda: ctx.update(runAsUser=0),
        "RestrictProcMount": lambda: ctx.update(procMount="Unmasked"),
        "RestrictSeccomp": lambda: spec["securityContext"].update(seccompProfile={"type": "Unconfined"}),
        "DisallowSELinuxOptions": lambda: ctx.update(seLinuxOptions={"user": "system_u"}),
        "RestrictSELinuxType": lambda: ctx.update(seLinuxOptions={"type": "spc_t"}),
        "DropAllCapabilities": lambda: ctx.update(capabilities={"drop": ["NET_RAW"]}),
        "RestrictCapabilities": lambda: ctx.update(capabilities={"drop": ["ALL"], "add": ["SYS_ADMIN"]}),
        "DisallowHostNamespaces": lambda: spec.update(hostNetwork=True),
        "RestrictHostPorts": lambda: first.update(ports=[{"containerPort": 6080, "hostPort": 6080}]),
        "RestrictVolumeTypes": lambda: spec["volumes"].append({"name": "nfs", "nfs": {"server": "x", "path": "/"}}),
        "RestrictHostPathWrite": lambda: (spec["volumes"].append({"name": "host", "hostPath": {"path": "/"}}),
                                          first["volumeMounts"].append({"name": "host", "mountPath": "/host"})),
        "RestrictIstioSidecarOverrides": lambda: annotations.update({"proxy.istio.io/config": "{}"}),
        "RestrictIstioTrafficOverrides": lambda: annotations.update({"sidecar.istio.io/inject": "false"}),
        "RestrictIstioAmbientOverrides": lambda: annotations.update({"ambient.istio.io/bypass-inbound-capture": "true"}),
        "RestrictIstioUser": lambda: ctx.update(runAsGroup=1337),
    }
    changes[policy]()
    return bad


def restrict_external_names(service):
    return ["ExternalName"] if service["spec"].get("type") == "ExternalName" else []


def disallow_node_port_services(service):
    return ["NodePort"] if service["spec"].get("type") == "NodePort" else []


def tak_pod_zero_trust(pod):
    """validatePodZeroTrust() of pepr/capabilities/tak-policy.ts: the first problem, or None.

    The webhook that calls it is bound to namespaces takserver and cloudtak,
    so it does not judge this pod. It is the repository's own bar and stricter
    than UDS Core in three points: no added capability at all, no seLinuxOptions,
    and no sidecar inject label of any value.
    """
    labels, annotations = pod["metadata"].get("labels", {}), pod["metadata"].get("annotations", {})
    if "sidecar.istio.io/inject" in labels or "sidecar.istio.io/inject" in annotations:
        return "sidecar.istio.io/inject is forbidden"
    forbidden = ("ambient.istio.io/bypass-inbound-capture", "proxy.istio.io/config",
                 "traffic.sidecar.istio.io/excludeInboundPorts", "traffic.sidecar.istio.io/excludeInterfaces",
                 "traffic.sidecar.istio.io/excludeOutboundIPRanges", "traffic.sidecar.istio.io/excludeOutboundPorts",
                 "traffic.sidecar.istio.io/includeInboundPorts", "traffic.sidecar.istio.io/includeOutboundIPRanges",
                 "traffic.sidecar.istio.io/includeOutboundPorts", "sidecar.istio.io/interceptionMode",
                 "traffic.sidecar.istio.io/kubevirtInterfaces", "istio.io/redirect-virtual-interfaces",
                 "sidecar.istio.io/bootstrapOverride", "sidecar.istio.io/discoveryAddress",
                 "sidecar.istio.io/proxyImage", "sidecar.istio.io/userVolume", "sidecar.istio.io/userVolumeMount")
    for key in forbidden:
        if key in annotations:
            return f"annotation {key} is forbidden"
    spec = pod["spec"]
    if spec.get("hostNetwork") or spec.get("hostPID") or spec.get("hostIPC"):
        return "host namespaces are forbidden"
    for volume in spec.get("volumes", []):
        for key in volume:
            if key != "name" and key not in UDS_VOLUME_TYPES:
                return f"volume type {key} is forbidden"

    def context_problem(ctx, scope):
        if not ctx:
            return None
        if 1337 in (ctx.get("runAsUser"), ctx.get("runAsGroup"), ctx.get("fsGroup")):
            return f"{scope} uses the Istio UID/GID 1337"
        if ctx.get("runAsUser") == 0 or ctx.get("runAsNonRoot") is False:
            return f"{scope} runs as root"
        if (ctx.get("seccompProfile") or {}).get("type") not in (None, "RuntimeDefault", "Localhost"):
            return f"{scope} seccompProfile is forbidden"
        if "seLinuxOptions" in ctx:
            return f"{scope} seLinuxOptions are forbidden"
        return None

    problem = context_problem(spec.get("securityContext"), "pod")
    if problem:
        return problem
    for container in all_containers(spec):
        name, ctx = container["name"], container.get("securityContext") or {}
        if name in ("istio-proxy", "istio-validation"):
            return f"container {name} is forbidden"
        problem = context_problem(ctx, f"container {name}")
        if problem:
            return problem
        if ctx.get("privileged") or ctx.get("allowPrivilegeEscalation"):
            return f"container {name} may escalate"
        if ctx.get("procMount") not in (None, "Default"):
            return f"container {name} procMount is forbidden"
        if (ctx.get("capabilities") or {}).get("add"):
            return f"container {name} adds capabilities"
        if any(isinstance(port.get("hostPort"), int) and port["hostPort"] > 0 for port in container.get("ports", [])):
            return f"container {name} has a hostPort"
    return None


# --- a small JSON Schema checker ---------------------------------------------

SCHEMA_ANNOTATIONS = {"$schema", "$comment", "$id", "id", "title", "description", "default", "examples", "definitions", "$defs"}


def schema_errors(value, schema, root=None, path="$"):
    """Problems of value under schema. Raises on a keyword it does not implement."""
    root = root or schema
    errors = []
    if "$ref" in schema:
        target = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            target = target[part]
        return schema_errors(value, target, root, path)
    known = {"type", "properties", "required", "additionalProperties", "patternProperties", "items", "enum", "pattern",
             "minLength", "maxLength", "minItems", "uniqueItems", "minProperties", "minimum", "maximum", "anyOf", "oneOf"}
    unknown = set(schema) - known - SCHEMA_ANNOTATIONS
    if unknown:
        raise NotImplementedError(f"schema keyword {sorted(unknown)} at {path}")
    types = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int, "number": (int, float),
             "null": type(None)}
    wanted = schema.get("type")
    if wanted:
        names = wanted if isinstance(wanted, list) else [wanted]
        ok = any(isinstance(value, types[name]) and not (name in ("integer", "number") and isinstance(value, bool))
                 for name in names)
        if not ok:
            return [f"{path}: expected {wanted}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: {value!r} does not match {schema['pattern']}")
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 1 << 30):
            errors.append(f"{path}: length {len(value)} is out of range")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get("minimum", value) or value > schema.get("maximum", value):
            errors.append(f"{path}: {value} is out of range")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if schema.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            errors.append(f"{path}: items are not unique")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                errors += schema_errors(item, schema["items"], root, f"{path}[{index}]")
    if isinstance(value, dict):
        if len(value) < schema.get("minProperties", 0):
            errors.append(f"{path}: fewer than {schema['minProperties']} properties")
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}: {name} is required")
        properties, patterns = schema.get("properties", {}), schema.get("patternProperties", {})
        extra = schema.get("additionalProperties", True)
        for name, item in value.items():
            if name in properties:
                errors += schema_errors(item, properties[name], root, f"{path}.{name}")
            elif any(re.search(pattern, name) for pattern in patterns):
                for pattern, sub in patterns.items():
                    if re.search(pattern, name) and isinstance(sub, dict):
                        errors += schema_errors(item, sub, root, f"{path}.{name}")
            elif extra is False:
                errors.append(f"{path}: property {name} is not allowed")
            elif isinstance(extra, dict):
                errors += schema_errors(item, extra, root, f"{path}.{name}")
    for keyword in ("anyOf", "oneOf"):
        if keyword in schema:
            passing = sum(1 for sub in schema[keyword] if not schema_errors(value, sub, root, path))
            if passing == 0 or (keyword == "oneOf" and passing != 1):
                errors.append(f"{path}: {passing} of the {keyword} alternatives match")
    return errors


# --- helpers shared by the tests and the script modes --------------------------

HEADER = ("Copyright 2026 uds-takserver authors", "SPDX-License-Identifier: GPL-3.0-only")


def header_problem(path):
    """None when the file starts with the licence header in its comment syntax."""
    with open(path, encoding="utf-8") as stream:
        lines = stream.read(2000).splitlines()
    if path.endswith(".json"):
        # JSON has no comments: the header is the "$comment" member.
        text = " ".join(lines)
        return None if all(part in text for part in HEADER) and '"$comment"' in text else "no $comment with the header"
    # A shebang, `# yamllint disable-file`, or the opening of a template or
    # HTML comment may come first.
    body = [line for line in lines if not re.match(r"^(#!.*|# yamllint disable-file|\{\{/\*|<!--)$", line)]
    stripped = [re.sub(r"^(# |// |<!-- )?(.*?)( -->)?$", r"\2", line) for line in body[:2]]
    return None if tuple(stripped) == HEADER else f"starts with {body[:2]!r}"


def action_commands(component_file):
    """(lifecycle path, shell, cmd) of every cmd action of a ZarfComponentConfig file."""
    component = load_yaml(component_file)[0]["component"]
    found = []
    for lifecycle, sets in (component.get("actions") or {}).items():
        for moment, actions in sets.items():
            if moment == "defaults":
                continue
            for index, action in enumerate(actions):
                if "cmd" in action:
                    shell = (action.get("shell") or {}).get("linux", "sh")
                    found.append((f"{lifecycle}.{moment}[{index}]", shell, action["cmd"], action))
    return found


def yq_projection(cmd):
    """The single-quoted yq expression of the identity copy action."""
    match = re.search(r"\./zarf tools yq -p json -o json '([^']+)'", cmd)
    if not match:
        raise AssertionError("the copy action has no ./zarf tools yq -p json -o json '<expression>'")
    return match.group(1)


def crd_schema(node):
    """An OpenAPI v3 CRD schema node as strict JSON Schema: unknown fields are errors."""
    if isinstance(node, list):
        return [crd_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key in ("x-kubernetes-preserve-unknown-fields", "x-kubernetes-int-or-string", "x-kubernetes-validations",
                   "x-kubernetes-list-type", "x-kubernetes-list-map-keys", "x-kubernetes-map-type", "format", "nullable"):
            continue
        if key == "properties":
            out[key] = {name: crd_schema(sub) for name, sub in value.items()}
        else:
            out[key] = crd_schema(value)
    if node.get("x-kubernetes-int-or-string"):
        out.pop("type", None)
        out["anyOf"] = [{"type": "integer"}, {"type": "string"}]
    if node.get("nullable") and isinstance(out.get("type"), str):
        out["type"] = [out["type"], "null"]
    if "properties" in out and "additionalProperties" not in out and not node.get("x-kubernetes-preserve-unknown-fields"):
        out["additionalProperties"] = False
    return out


def write_crd_schemas(out_dir, crd_files):
    for path in crd_files:
        with open(path, encoding="utf-8") as stream:
            crd = json.load(stream)
        group, kind = crd["spec"]["group"], crd["spec"]["names"]["kind"].lower()
        for version in crd["spec"]["versions"]:
            schema = crd_schema(version["schema"]["openAPIV3Schema"])
            # The API server adds these to every object.
            schema.setdefault("properties", {}).update({"apiVersion": {"type": "string"}, "kind": {"type": "string"},
                                                        "metadata": {"type": "object"}})
            target = os.path.join(out_dir, group, f"{kind}_{version['name']}.json")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as stream:
                json.dump(schema, stream, indent=1, sort_keys=True)
            print(f"{target}: {group}/{version['name']} {crd['spec']['names']['kind']}, "
                  f"CRD resourceVersion {crd['metadata'].get('resourceVersion', '?')}")


def need_uds(test):
    return unittest.skipUnless(UDS, "no uds binary (set UDS)")(test)


# --- tests -------------------------------------------------------------------


@need_uds
class Composition(unittest.TestCase):
    """zarf.yaml and its component files."""

    @classmethod
    def setUpClass(cls):
        cls.package = load_yaml(os.path.join(HERE, "zarf.yaml"))[0]
        cls.components = {}
        for component in cls.package["components"]:
            paths = [entry["path"] for entry in component["import"]["local"]]
            cls.components[component["name"]] = load_yaml(os.path.join(HERE, paths[0]))[0]

    def test_root(self):
        package = self.package
        self.assertEqual(package["apiVersion"], "zarf.dev/v1beta1")
        self.assertEqual(package["kind"], "ZarfPackageConfig")
        self.assertEqual(package["metadata"]["name"], "takx-desktop")
        self.assertEqual(package["metadata"]["architecture"], "amd64")
        self.assertEqual(package["metadata"]["version"], "takx-build-tag")
        self.assertEqual(package["values"], {"files": ["values.yaml"], "schema": "values.schema.json"})
        self.assertNotIn("variables", package)
        self.assertEqual(sorted(package["attachments"]),
                         ["DEBIAN-PACKAGES", "LICENCE-GATE", "LICENSES", "NOTICE", "PATCHES", "README", "SOURCES"])

    def test_component_order_and_selection(self):
        names = [component["name"] for component in self.package["components"]]
        # The identity is handed over before the desktop starts.
        self.assertEqual(names, ["takx-desktop-image", "takx-desktop-takclient", "takx-desktop-identity", "takx-desktop"])
        optional = {component["name"]: component.get("optional", False) for component in self.package["components"]}
        self.assertEqual(optional, {"takx-desktop-image": False, "takx-desktop-takclient": True,
                                    "takx-desktop-identity": True, "takx-desktop": False})
        for component in self.package["components"]:
            # Everything but name, description, selection and the import lives in the component file.
            self.assertLessEqual(set(component), {"name", "description", "optional", "selector", "import"})
            self.assertTrue(component["description"])
            self.assertEqual(len(component["import"]["local"]), 1)
        self.assertEqual(self.package["components"][0]["selector"], {"architecture": "amd64"})

    def test_component_files(self):
        for name, config in self.components.items():
            self.assertEqual(config["apiVersion"], "zarf.dev/v1beta1")
            self.assertEqual(config["kind"], "ZarfComponentConfig")
            self.assertEqual(config["metadata"]["name"], name)
            # Zarf 1.0.1 rejects these inside a component file.
            self.assertFalse({"selector", "optional", "description", "name"} & set(config["component"]))

    def test_image_component(self):
        images = self.components["takx-desktop-image"]["component"]["images"]
        self.assertEqual(images, [{"name": f"{IMAGE}:takx-build-tag", "id": "desktop", "source": "registry"}])
        self.assertEqual(set(self.components["takx-desktop-image"]["component"]), {"images"})

    def test_desktop_component(self):
        component = self.components["takx-desktop"]["component"]
        self.assertNotIn("images", component)
        (chart,) = component["charts"]
        self.assertEqual(chart["name"], "takx-desktop")
        self.assertEqual(chart["namespace"], "takx-desktop")
        self.assertEqual(os.path.normpath(os.path.join(HERE, "components", chart["local"]["path"])), CHART)
        self.assertEqual(chart["valuesFiles"], [{"path": "../image-values.yaml", "enableTemplating": True}])
        self.assertEqual(chart["values"], [
            {"sourcePath": ".global.domain", "targetPath": ".domain"},
            {"sourcePath": ".desktop", "targetPath": ".", "excludePaths": [".desktop.image"]},
        ])
        (image_values,) = load_yaml(os.path.join(HERE, "image-values.yaml"))
        self.assertEqual(list(image_values), ["image"])
        self.assertIn('.Pkg.GetComponent "takx-desktop-image"', image_values["image"])
        self.assertIn('.GetImageByID "desktop"', image_values["image"])
        waits = [action["wait"]["cluster"] for action in component["actions"]["onDeploy"]["onSuccess"]]
        self.assertEqual([wait["kind"] for wait in waits], ["packages.uds.dev", "deployment"])
        self.assertTrue(all(wait["namespace"] == "takx-desktop" and wait["name"] == "takx-desktop" for wait in waits))

    def test_takclient_manifest(self):
        component = self.components["takx-desktop-takclient"]["component"]
        (manifest,) = component["manifests"]
        self.assertEqual(manifest["namespace"], "takclients")
        self.assertTrue(manifest["enableTemplating"])
        self.assertNotIn("actions", component)
        (client,) = load_yaml(os.path.join(HERE, "manifests", "takclient.yaml"))
        self.assertEqual(client["apiVersion"], "tak.uds.dev/v1alpha1")
        self.assertEqual(client["kind"], "TakClient")
        self.assertEqual(client["metadata"]["namespace"], "takclients")
        self.assertEqual(client["metadata"]["name"], "{{ .Values.identity.name }}")
        self.assertEqual(client["spec"], {"callsign": "{{ .Values.identity.callsign }}",
                                          "groups": ["{{ .Values.identity.group }}"]})

    def test_package_values_fit_their_schema(self):
        (values,) = load_yaml(os.path.join(HERE, "values.yaml"))
        with open(os.path.join(HERE, "values.schema.json"), encoding="utf-8") as stream:
            schema = json.load(stream)
        self.assertEqual(schema_errors(values, schema), [])
        # A TakClient created with kubectl and named takx-desktop already exists
        # where this was developed; Helm would refuse to adopt it.
        self.assertEqual(values["identity"], {"name": "takx-browser", "callsign": "takx-browser", "group": "Operators"})
        self.assertEqual(values["desktop"], {})
        for bad in ({"name": "a; rm -rf /"}, {"name": "$(id)"}, {"name": "Upper"}, {"callsign": "abc"},
                    {"group": "a/b"}, {"extra": "x"}):
            changed = copy.deepcopy(values)
            changed["identity"].update(bad)
            self.assertTrue(schema_errors(changed, schema), bad)

    def test_placeholder_and_substitution_targets(self):
        # create-package.sh replaces the placeholder in exactly these files.
        for path in (os.path.join(HERE, "zarf.yaml"), os.path.join(HERE, "components", "image.yaml"),
                     os.path.join(CHART, "values.yaml")):
            with open(path, encoding="utf-8") as stream:
                self.assertIn("takx-build-tag", stream.read(), path)


class Staging(unittest.TestCase):
    """create-package.sh --stage-only: the image is pinned by digest. Needs no Docker and no uds."""

    DIGEST = "sha256:" + "ab" * 32
    TREE = "cd" * 32

    def stage(self, tmp, tag, env):
        notices = os.path.join(tmp, "notices")
        if not os.path.isdir(notices):
            os.makedirs(os.path.join(notices, "LICENSES"))
            os.makedirs(os.path.join(notices, "patches"))
            os.makedirs(os.path.join(notices, "locks"))
            for name in ("NOTICE.md", "licence-gate.json"):
                with open(os.path.join(notices, name), "w", encoding="utf-8") as stream:
                    stream.write("{}\n")
            with open(os.path.join(notices, "SOURCES.json"), "w", encoding="utf-8") as stream:
                json.dump({"tree": {"manifest_sha256": self.TREE}}, stream)
        out = os.path.join(tmp, "stage-%d" % len(os.listdir(tmp)))
        inherited = {key: value for key, value in os.environ.items()
                     if key not in ("TAKX_IMAGE_DIGEST", "TAKX_IMAGE_SOURCE")}
        done = subprocess.run((os.path.join(HERE, "create-package.sh"), tag, "--stage-only", out),
                              capture_output=True, check=False, text=True,
                              env={**inherited, "TAKX_NOTICES_DIR": notices, **env})
        return done, out

    def test_registry_source_is_pinned_by_the_digest_the_caller_names(self):
        tag = "5.7-0b59a9bb-proto-" + self.TREE[:12]
        with tempfile.TemporaryDirectory() as tmp:
            done, out = self.stage(tmp, tag, {"TAKX_IMAGE_SOURCE": "registry", "TAKX_IMAGE_DIGEST": self.DIGEST})
            self.assertEqual(done.returncode, 0, done.stderr)
            reference = f"{IMAGE}:{tag}@{self.DIGEST}"
            with open(os.path.join(out, "zarf", "components", "image.yaml"), encoding="utf-8") as stream:
                component = stream.read()
            self.assertIn(f"- name: {reference}\n", component)
            self.assertIn("source: registry", component)
            with open(os.path.join(out, "chart", "values.yaml"), encoding="utf-8") as stream:
                self.assertIn(f"\nimage: {reference}\n", stream.read())
            with open(os.path.join(out, "zarf", "zarf.yaml"), encoding="utf-8") as stream:
                package = stream.read()
            self.assertIn(f'version: "{tag}"', package)
            self.assertNotIn("@sha256", package)
            for folder, _dirs, names in os.walk(out):
                for name in names:
                    if name.endswith((".yaml", ".json", ".md")):
                        with open(os.path.join(folder, name), encoding="utf-8") as stream:
                            self.assertNotIn("takx-build-tag", stream.read(), name)

    def test_no_digest_no_package(self):
        tag = "5.7-0b59a9bb-proto-" + self.TREE[:12]
        with tempfile.TemporaryDirectory() as tmp:
            for env in ({"TAKX_IMAGE_SOURCE": "registry"},
                        {"TAKX_IMAGE_SOURCE": "registry", "TAKX_IMAGE_DIGEST": "sha256:abc"},
                        {"TAKX_IMAGE_SOURCE": "registry", "TAKX_IMAGE_DIGEST": "latest"}):
                done, out = self.stage(tmp, tag, env)
                self.assertNotEqual(done.returncode, 0, env)
                self.assertIn("No image digest", done.stderr)
                self.assertFalse(os.path.exists(out))

    def test_tag_must_name_the_tree_of_the_notices(self):
        with tempfile.TemporaryDirectory() as tmp:
            done, out = self.stage(tmp, "5.7-0b59a9bb-proto-000000000000",
                                   {"TAKX_IMAGE_SOURCE": "registry", "TAKX_IMAGE_DIGEST": self.DIGEST})
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("does not end in", done.stderr)
            self.assertFalse(os.path.exists(out))


@need_uds
class BuiltArchive(unittest.TestCase):
    """archive_facts on synthetic decompressed packages."""

    DIGEST = "sha256:" + "ab" * 32

    def archive(self, tmp, documents, manifests, blob=True):
        os.makedirs(os.path.join(tmp, "images", "blobs", "sha256"))
        with open(os.path.join(tmp, "zarf.yaml"), "w", encoding="utf-8") as stream:
            stream.write("\n---\n".join(json.dumps(document) for document in documents) + "\n")
        with open(os.path.join(tmp, "images", "index.json"), "w", encoding="utf-8") as stream:
            json.dump({"schemaVersion": 2, "manifests": [{"digest": digest} for digest in manifests]}, stream)
        if blob:
            with open(os.path.join(tmp, "images", "blobs", "sha256", self.DIGEST.split(":")[1]), "w", encoding="utf-8") as stream:
                stream.write("{}")
        return archive_facts(tmp, self.DIGEST)

    def documents(self, reference):
        return [{"apiVersion": "zarf.dev/v1alpha1", "components": [{"name": "i", "images": [reference]}, {"name": "c"}]},
                {"apiVersion": "zarf.dev/v1beta1",
                 "components": [{"name": "i", "images": [{"name": reference, "id": "desktop"}]}, {"name": "c"}]}]

    def test_a_pinned_package_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            checks, facts = self.archive(tmp, self.documents(f"{IMAGE}:5.7-x@{self.DIGEST}"), [self.DIGEST])
            self.assertTrue(all(checks.values()), checks)
            self.assertEqual(facts["apiVersions"], ["zarf.dev/v1alpha1", "zarf.dev/v1beta1"])

    def test_tag_only_other_digest_and_other_manifest_fail(self):
        other = "sha256:" + "cd" * 32
        cases = ((self.documents(f"{IMAGE}:5.7-x"), [self.DIGEST], True),
                 (self.documents(f"{IMAGE}:5.7-x@{other}"), [self.DIGEST], True),
                 (self.documents(f"{IMAGE}:5.7-x@{self.DIGEST}"), [other], True),
                 (self.documents(f"{IMAGE}:5.7-x@{self.DIGEST}"), [self.DIGEST, other], True),
                 (self.documents(f"{IMAGE}:5.7-x@{self.DIGEST}"), [self.DIGEST], False),
                 (self.documents(f"{IMAGE}:5.7-x@{self.DIGEST}")[1:], [self.DIGEST], True))
        for documents, manifests, blob in cases:
            with tempfile.TemporaryDirectory() as tmp:
                checks, _facts = self.archive(tmp, documents, manifests, blob)
                self.assertFalse(all(checks.values()), (documents, manifests, blob))


@need_uds
class IdentityAction(unittest.TestCase):
    """The component that copies two keys of the TakClient Secret."""

    @classmethod
    def setUpClass(cls):
        cls.path = os.path.join(HERE, "components", "identity.yaml")
        cls.component = load_yaml(cls.path)[0]["component"]
        cls.actions = action_commands(cls.path)

    def synthetic(self):
        return {
            "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
            "metadata": {
                "name": "takx-browser", "namespace": "takclients", "uid": "u", "resourceVersion": "9",
                "labels": {"app.kubernetes.io/managed-by": "takserver-oidc", "tak.uds.dev/takclient": "takx-browser"},
                "annotations": {"tak.uds.dev/fingerprint": "AA:BB", "tak.uds.dev/not-after": "2026-12-25T00:00:00.000Z",
                                "tak.uds.dev/serial": "1234", "tak.uds.dev/callsign": "takx-browser"},
                "managedFields": [{"manager": "pepr"}],
            },
            "data": {"ca.pem": "Q0E=", "client.key": "S0VZ", "client.pem": "UEVN", "package.zip": "WklQ", "password": "UFc="},
        }

    def project(self, secret):
        expression = yq_projection(self.actions[1][2])
        return run_uds("zarf", "tools", "yq", "-p", "json", "-o", "json", expression, "-", data=json.dumps(secret).encode())

    def test_order_and_shape(self):
        self.assertEqual([path for path, *_ in self.actions],
                         ["onDeploy.onSuccess[0]", "onDeploy.onSuccess[1]", "onRemove.before[0]"])
        wait, copy_action, remove = (action for *_, action in self.actions)
        self.assertIn('wait "takclient/{{ .Values.identity.name }}" -n takclients --for=condition=Ready', wait["cmd"])
        self.assertTrue(wait["enableTemplating"])
        self.assertTrue(copy_action["enableTemplating"])
        self.assertTrue(copy_action["silent"])
        self.assertEqual(copy_action["shell"]["linux"], "bash")
        self.assertTrue(copy_action["cmd"].startswith("set -euo pipefail\n"))
        self.assertIn("delete secret takx-desktop-identity -n takx-desktop --ignore-not-found", remove["cmd"])
        # The manifest exists so that the namespace is there before the copy.
        (manifest,) = self.component["manifests"]
        self.assertEqual(manifest["namespace"], "takx-desktop")

    def test_secret_never_reaches_a_file_or_the_terminal(self):
        cmd = self.actions[1][2]
        body = cmd.split("\n", 1)[1]
        stages = [stage.strip() for stage in re.sub(r"'[^']*'", "''", body).split("|")]
        self.assertEqual(len(stages), 3)
        self.assertTrue(stages[0].startswith("./zarf tools kubectl get secret "))
        self.assertTrue(stages[1].startswith("./zarf tools yq "))
        self.assertTrue(stages[2].startswith("./zarf tools kubectl apply --server-side "))
        self.assertTrue(stages[2].endswith("-f - >/dev/null"))
        for word in ("tee", "echo", "cat ", "base64", "$(", "`", ">/tmp", "mktemp"):
            self.assertNotIn(word, re.sub(r"'[^']*'", "''", body), word)

    def test_only_template_is_the_takclient_name(self):
        for _path, _shell, cmd, _action in self.actions:
            self.assertEqual(set(re.findall(r"\{\{[^}]*\}\}", cmd)) - {"{{ .Values.identity.name }}"}, set())
            # Go templates would read any other "{{" as an action.
            self.assertEqual(cmd.count("{{"), cmd.count("{{ .Values.identity.name }}"))

    def test_tools_are_called_through_zarf(self):
        # lint:zarf-tools of uds-common: no bare kubectl, helm or yq in an action.
        for _path, _shell, cmd, _action in self.actions:
            plain = re.sub(r"\./zarf tools (kubectl|yq|helm)", "TOOL", re.sub(r"'[^']*'", "''", cmd))
            self.assertIsNone(re.search(r"(^|[\s;|&(])(kubectl|helm|yq)([\s;|&)]|$)", plain), cmd)

    def test_projection_copies_two_keys(self):
        result = json.loads(self.project(self.synthetic()))
        self.assertEqual(result["data"], {"package.zip": "WklQ", "password": "UFc="})
        self.assertEqual(result["type"], "Opaque")
        self.assertEqual(result["metadata"], {
            "name": "takx-desktop-identity", "namespace": "takx-desktop",
            "labels": {"app.kubernetes.io/part-of": "takx-desktop", "uds.dev/pod-reload": "true"},
            "annotations": {"uds.dev/pod-reload-selector": "app.kubernetes.io/name=takx-desktop",
                            "tak.uds.dev/takclient": "takx-browser", "tak.uds.dev/fingerprint": "AA:BB",
                            "tak.uds.dev/not-after": "2026-12-25T00:00:00.000Z"},
        })
        text = json.dumps(result)
        for absent in ("S0VZ", "UEVN", "Q0E=", "client.key", "client.pem", "ca.pem", "managedFields", "resourceVersion", "1234"):
            self.assertNotIn(absent, text)

    def test_projection_refuses_an_incomplete_secret(self):
        for missing in ("package.zip", "password"):
            secret = self.synthetic()
            del secret["data"][missing]
            self.assertEqual(self.project(secret).strip(), "", missing)

    def test_projection_without_annotations(self):
        secret = self.synthetic()
        del secret["metadata"]["annotations"]
        result = json.loads(self.project(secret))
        self.assertEqual(result["metadata"]["annotations"]["tak.uds.dev/fingerprint"], "")
        self.assertEqual(result["metadata"]["annotations"]["tak.uds.dev/takclient"], "takx-browser")

    def render(self, cmd, name):
        """The action command after Go templating, through the Helm that uds vendors.

        Zarf renders `enableTemplating` commands with Go's text/template too;
        this shows that the parser takes the text (the braces of the yq
        expression are not template actions) and what comes out. It is not
        Zarf's own renderer.
        """
        with tempfile.TemporaryDirectory() as chart:
            os.mkdir(os.path.join(chart, "templates"))
            with open(os.path.join(chart, "Chart.yaml"), "w", encoding="utf-8") as stream:
                stream.write("apiVersion: v2\nname: action\nversion: 0.0.0\n")
            # Helm only renders manifests, so the command travels as a block scalar.
            with open(os.path.join(chart, "templates", "cmd.yaml"), "w", encoding="utf-8") as stream:
                stream.write("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: cmd\ndata:\n  cmd: |\n")
                stream.write("".join(f"    {line}\n" for line in cmd.rstrip("\n").split("\n")))
            out = run_uds("zarf", "tools", "helm", "template", "action", chart, "--set", f"identity.name={name}")
        return one(load_yaml_text(out), "ConfigMap")["data"]["cmd"]

    def run_copy(self, secret_json, get_status=0):
        """Run the rendered copy command with stand-ins for the two kubectl calls; yq is the real one."""
        rendered = self.render(self.actions[1][2], "takx-browser")
        self.assertNotIn("{{", rendered)
        self.assertIn('get secret "takx-browser" -n takclients -o json', rendered)
        with tempfile.TemporaryDirectory() as work:
            with open(os.path.join(work, "secret.json"), "w", encoding="utf-8") as stream:
                stream.write(secret_json)
            shim = os.path.join(work, "zarf")
            with open(shim, "w", encoding="utf-8") as stream:
                stream.write(f"""#!/usr/bin/env bash
# Stand-in for the Zarf binary an action calls as ./zarf.
set -euo pipefail
[[ "$1" == tools ]]
case "$2 $3" in
  "kubectl get") echo "$*" >>calls.txt; cat secret.json; exit {get_status};;
  "kubectl apply")
    echo "$*" >>calls.txt
    cat >applied.json
    # kubectl: "error: no objects passed to apply"
    [[ -s applied.json ]] || {{ echo "error: no objects passed to apply" >&2; exit 1; }}
    echo "secret/takx-desktop-identity serverside-applied";;
  "yq -p") shift 2; exec {shlex.quote(UDS)} zarf tools yq "$@";;
  *) echo "unexpected: $*" >&2; exit 97;;
esac
""")
            os.chmod(shim, 0o755)
            done = subprocess.run(("bash", "-c", rendered), cwd=work, capture_output=True, check=False,
                                  env={"PATH": os.environ["PATH"], "HOME": work, "KUBECONFIG": "/dev/null"})
            applied = None
            if os.path.exists(os.path.join(work, "applied.json")):
                with open(os.path.join(work, "applied.json"), encoding="utf-8") as stream:
                    applied = stream.read()
            with open(os.path.join(work, "calls.txt"), encoding="utf-8") as stream:
                calls = stream.read().splitlines()
        return done, applied, calls

    def test_rendered_command_runs_as_a_pipeline(self):
        done, applied, calls = self.run_copy(json.dumps(self.synthetic()))
        self.assertEqual(done.returncode, 0, done.stderr)
        # Nothing on the terminal, not even kubectl's confirmation.
        self.assertEqual(done.stdout, b"")
        self.assertEqual(done.stderr, b"")
        # The stages of a pipeline start together, so the order of the two lines is not fixed.
        self.assertEqual(sorted(calls), [
            "tools kubectl apply --server-side --force-conflicts --field-manager=takx-desktop-identity -f -",
            "tools kubectl get secret takx-browser -n takclients -o json"])
        result = json.loads(applied)
        self.assertEqual(sorted(result["data"]), ["package.zip", "password"])
        self.assertEqual((result["metadata"]["namespace"], result["metadata"]["name"]),
                         ("takx-desktop", "takx-desktop-identity"))

    def test_rendered_command_fails_closed(self):
        # kubectl get fails after writing output: only pipefail makes the action fail,
        # because the two later stages succeed.
        done, applied, _calls = self.run_copy(json.dumps(self.synthetic()), get_status=1)
        self.assertNotEqual(done.returncode, 0)
        self.assertTrue(applied)
        # kubectl get fails with no output.
        done, _applied, _calls = self.run_copy("", get_status=1)
        self.assertNotEqual(done.returncode, 0)
        # A Secret without one of the two keys: nothing is passed on.
        secret = self.synthetic()
        del secret["data"]["password"]
        done, applied, _calls = self.run_copy(json.dumps(secret))
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(applied, "")
        self.assertNotIn(b"WklQ", done.stdout + done.stderr)

    def test_wait_command_renders(self):
        rendered = self.render(self.actions[0][2], "takx-browser")
        self.assertEqual(rendered.strip(), './zarf tools kubectl wait "takclient/takx-browser" -n takclients '
                                           "--for=condition=Ready --timeout=300s")

    def test_names_match_the_chart(self):
        (values,) = load_yaml(os.path.join(CHART, "values.yaml"))
        self.assertEqual(values["identity"]["secretName"], "takx-desktop-identity")
        (info,) = load_yaml(os.path.join(HERE, "manifests", "identity-info.yaml"))
        self.assertEqual(info["kind"], "ConfigMap")
        self.assertEqual(info["data"]["secret"], "takx-desktop-identity")
        self.assertNotIn("binaryData", info)


@need_uds
class Chart(unittest.TestCase):
    """What the chart renders with its default values and with every branch on."""

    @classmethod
    def setUpClass(cls):
        cls.default = helm_template()
        cls.full = helm_template(ALL_VALUES)

    def test_kinds(self):
        self.assertEqual(sorted(doc["kind"] for doc in self.default),
                         ["AuthorizationPolicy", "Deployment", "DestinationRule", "NetworkPolicy", "Package", "Service",
                          "ServiceAccount"])
        self.assertEqual(sorted(doc["kind"] for doc in self.full),
                         ["AuthorizationPolicy", "Deployment", "DestinationRule", "NetworkPolicy", "Package",
                          "PersistentVolumeClaim", "Service", "ServiceAccount"])
        for doc in self.default + self.full:
            self.assertEqual(doc["metadata"]["namespace"], "takx-desktop")
            self.assertNotIn(doc["kind"], ("Secret", "Role", "RoleBinding", "ClusterRole", "ClusterRoleBinding",
                                           "Exemption"))

    def test_ingress_isolation(self):
        """The JVM listens on 4242, 8087 and an ephemeral port: nothing but 6080 may be reached."""
        for documents in (self.default, self.full):
            policy = one(documents, "NetworkPolicy")
            self.assertEqual(policy["apiVersion"], "networking.k8s.io/v1")
            # Selects the pod, isolates it for ingress, allows nothing: the
            # only allowance is the operator's, from `expose`.
            self.assertEqual(policy["spec"], {"podSelector": {"matchLabels": SELECTOR}, "policyTypes": ["Ingress"]})
            deny = one(documents, "AuthorizationPolicy")
            self.assertEqual(deny["apiVersion"], "security.istio.io/v1")
            self.assertEqual(deny["spec"], {"selector": {"matchLabels": SELECTOR}, "action": "DENY",
                                            "rules": [{"to": [{"operation": {"notPorts": ["6080"]}}]}]})
            package = one(documents, "Package")
            (expose,) = package["spec"]["network"]["expose"]
            self.assertEqual(expose["port"], 6080)
            self.assertEqual([rule for rule in package["spec"]["network"].get("allow", []) if rule["direction"] != "Egress"], [])
            pod = one(documents, "Deployment")["spec"]["template"]["spec"]
            declared = [port for container in all_containers(pod) for port in container.get("ports", [])]
            self.assertEqual([port["containerPort"] for port in declared], [6080])
        without = helm_template({"network": {"denyOtherPorts": False}})
        self.assertEqual([doc["kind"] for doc in without if doc["kind"] == "AuthorizationPolicy"], [])
        self.assertEqual(one(without, "NetworkPolicy")["spec"]["policyTypes"], ["Ingress"])

    def test_bridge_connections_are_not_pooled(self):
        """websockify keeps one process per connection: the waypoint must not pool them."""
        for documents in (self.default, self.full):
            rule = one(documents, "DestinationRule")
            self.assertEqual(rule["apiVersion"], "networking.istio.io/v1")
            service = one(documents, "Service")
            self.assertEqual(rule["spec"]["host"],
                             f"{service['metadata']['name']}.{service['metadata']['namespace']}.svc.cluster.local")
            self.assertEqual(rule["spec"]["trafficPolicy"],
                             {"connectionPool": {"http": {"maxRequestsPerConnection": 1, "idleTimeout": "30s"}}})

    def test_texts_do_not_claim_that_the_desktop_has_no_tcp_port(self):
        for path in (os.path.join(CHART, "templates", "deployment.yaml"), os.path.join(CHART, "templates", "service.yaml"),
                     os.path.join(os.path.dirname(os.path.abspath(__file__)), "README.md")):
            with open(path, encoding="utf-8") as stream:
                text = stream.read()
            self.assertNotRegex(text, r"No (listening )?TCP port")
            self.assertIn("4242", text)
            self.assertIn("8087", text)

    def test_default_values_fit_the_schema(self):
        (values,) = load_yaml(os.path.join(CHART, "values.yaml"))
        with open(os.path.join(CHART, "values.schema.json"), encoding="utf-8") as stream:
            schema = json.load(stream)
        self.assertEqual(schema_errors(values, schema), [])
        self.assertEqual(sorted(schema["required"]), sorted(values))
        for path, bad in (("sso", {"groups": []}), ("display", {"depth": 8}), ("display", {"geometry": "big"}),
                          ("egress", {"hosts": ["*.example.com"]}), ("tak", {"certName": "bad name"}),
                          ("sso", {"clientId": "a:b"}), ("identity", {"secretName": "Bad_Name"})):
            changed = copy.deepcopy(values)
            changed[path].update(bad)
            self.assertTrue(schema_errors(changed, schema), (path, bad))

    def test_deployment(self):
        deployment = one(self.default, "Deployment")
        spec = deployment["spec"]
        self.assertEqual(spec["replicas"], 1)
        self.assertEqual(spec["strategy"], {"type": "Recreate"})
        self.assertEqual(spec["selector"]["matchLabels"], SELECTOR)
        pod = spec["template"]
        self.assertEqual(pod["metadata"]["labels"], {**SELECTOR, "app.kubernetes.io/part-of": "takx-desktop"})
        self.assertNotIn("annotations", pod["metadata"])
        pod_spec = pod["spec"]
        self.assertEqual(pod_spec["serviceAccountName"], "takx-desktop")
        self.assertIs(pod_spec["automountServiceAccountToken"], False)
        self.assertIs(pod_spec["enableServiceLinks"], False)
        self.assertEqual(pod_spec["nodeSelector"], {"kubernetes.io/arch": "amd64"})
        self.assertEqual(pod_spec["securityContext"], {
            "runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001, "fsGroup": 10001,
            "fsGroupChangePolicy": "OnRootMismatch", "seccompProfile": {"type": "RuntimeDefault"}})
        for key in ("hostNetwork", "hostPID", "hostIPC", "hostAliases", "shareProcessNamespace"):
            self.assertNotIn(key, pod_spec)
        self.assertEqual([c["name"] for c in pod_spec["initContainers"]], ["identity"])
        self.assertEqual([c["name"] for c in pod_spec["containers"]], ["desktop", "web"])

    def test_containers(self):
        pod_spec = one(self.default, "Deployment")["spec"]["template"]["spec"]
        containers = {c["name"]: c for c in all_containers(pod_spec)}
        base = {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}}
        self.assertEqual(containers["identity"]["securityContext"], base)
        self.assertEqual(containers["desktop"]["securityContext"], base)
        # The bridge is another user; the pod's fsGroup lets it open the RFB socket.
        self.assertEqual(containers["web"]["securityContext"], {**base, "runAsUser": 10002, "runAsGroup": 10002})
        for name, mode in (("identity", "identity"), ("desktop", "desktop"), ("web", "web")):
            container = containers[name]
            self.assertEqual(container["args"], [mode])
            self.assertNotIn("command", container)
            self.assertEqual(container["image"], f"{IMAGE}:takx-build-tag")
            self.assertEqual(set(container["resources"]), {"requests", "limits"})
            self.assertEqual(set(container["resources"]["limits"]), {"cpu", "memory"})
            self.assertNotIn("envFrom", container)
            for variable in container.get("env", []):
                self.assertNotIn("valueFrom", variable)
        self.assertNotIn("ports", containers["desktop"])
        self.assertEqual(containers["web"]["ports"], [{"name": "http", "containerPort": 6080, "protocol": "TCP"}])
        env = {name: {v["name"]: v["value"] for v in c.get("env", [])} for name, c in containers.items()}
        self.assertEqual(env["identity"], {"TAKX_IDENTITY_CONNECT_HOST": "takserver"})
        self.assertEqual(env["desktop"], {"EXTRA_JVM_OPTS": "-Xmx2g", "TAKX_GEOMETRY": "1920x1080", "TAKX_DEPTH": "24"})
        self.assertEqual(env["web"], {})
        probes = containers["desktop"]
        self.assertEqual(probes["startupProbe"]["exec"]["command"], ["/opt/takx-desktop/healthcheck.sh", "started"])
        self.assertEqual(probes["startupProbe"]["periodSeconds"] * probes["startupProbe"]["failureThreshold"], 600)
        self.assertEqual(probes["readinessProbe"]["exec"]["command"], ["/opt/takx-desktop/healthcheck.sh", "ready"])
        self.assertEqual(probes["livenessProbe"]["exec"]["command"], ["/opt/takx-desktop/healthcheck.sh", "live"])
        for probe in ("readinessProbe", "livenessProbe"):
            self.assertEqual(containers["web"][probe]["exec"]["command"], ["/opt/takx-desktop/healthcheck.sh", "web"])

    def test_volumes_and_the_secret_mount(self):
        pod_spec = one(self.default, "Deployment")["spec"]["template"]["spec"]
        volumes = {volume["name"]: volume for volume in pod_spec["volumes"]}
        self.assertEqual(volumes["tmp"], {"name": "tmp", "emptyDir": {"sizeLimit": "1Gi"}})
        self.assertEqual(volumes["session"], {"name": "session", "emptyDir": {"sizeLimit": "2Gi"}})
        self.assertEqual(volumes["vnc"], {"name": "vnc", "emptyDir": {"medium": "Memory", "sizeLimit": "1Mi"}})
        self.assertEqual(volumes["identity"], {"name": "identity", "emptyDir": {"medium": "Memory", "sizeLimit": "8Mi"}})
        self.assertEqual(volumes["identity-secret"], {"name": "identity-secret", "secret": {
            "secretName": "takx-desktop-identity", "optional": True, "defaultMode": 0o440}})
        # Written in decimal: not every YAML reader takes 0440 as octal.
        self.assertEqual(0o440, 288)
        self.assertEqual(len(volumes), 5)
        mounts = {c["name"]: {m["name"]: m for m in c["volumeMounts"]} for c in all_containers(pod_spec)}
        self.assertEqual(mounts["identity"], {
            "identity-secret": {"name": "identity-secret", "mountPath": "/run/takx/secret", "readOnly": True},
            "identity": {"name": "identity", "mountPath": "/run/takx/identity"}})
        self.assertEqual(mounts["desktop"], {
            "tmp": {"name": "tmp", "mountPath": "/tmp"},
            "session": {"name": "session", "mountPath": "/run/takx/session"},
            "vnc": {"name": "vnc", "mountPath": "/run/takx/vnc"},
            "identity": {"name": "identity", "mountPath": "/run/takx/identity", "readOnly": True}})
        # The container that faces the gateway sees the socket directory only.
        self.assertEqual(mounts["web"], {"vnc": {"name": "vnc", "mountPath": "/run/takx/vnc", "readOnly": True}})
        users = [name for name, mounted in mounts.items() if "identity-secret" in mounted]
        self.assertEqual(users, ["identity"])

    def test_service_and_account(self):
        service = one(self.default, "Service")
        self.assertEqual(service["spec"]["type"], "ClusterIP")
        self.assertEqual(service["spec"]["selector"], SELECTOR)
        self.assertEqual(service["spec"]["ports"], [{"name": "http", "port": 6080, "targetPort": "http", "protocol": "TCP"}])
        account = one(self.default, "ServiceAccount")
        self.assertIs(account["automountServiceAccountToken"], False)
        self.assertEqual(account["metadata"]["name"], "takx-desktop")

    def test_uds_package_default(self):
        package = one(self.default, "Package")
        self.assertEqual(package["apiVersion"], "uds.dev/v1alpha1")
        self.assertEqual(package["metadata"]["name"], "takx-desktop")
        spec = package["spec"]
        self.assertEqual(set(spec), {"sso", "network"})
        (client,) = spec["sso"]
        self.assertEqual(client["clientId"], "uds-takx-desktop")
        self.assertEqual(client["redirectUris"], ["https://takx.uds.dev/login"])
        self.assertEqual(client["enableAuthserviceSelector"], SELECTOR)
        self.assertEqual(client["groups"], {"anyOf": ["/UDS Core/Admin"]})
        for key in ("publicClient", "secret", "secretConfig", "standardFlowEnabled", "serviceAccountsEnabled", "attributes"):
            self.assertNotIn(key, client)
        network = spec["network"]
        self.assertEqual(network["serviceMesh"], {"mode": "ambient"})
        (expose,) = network["expose"]
        self.assertEqual({key: expose[key] for key in ("service", "selector", "gateway", "host", "port")},
                         {"service": "takx-desktop", "selector": SELECTOR, "gateway": "tenant", "host": "takx", "port": 6080})
        # Exactly one way out: TAK streaming through the passthrough gateway.
        self.assertEqual(network["allow"], [
            {"direction": "Egress", "selector": SELECTOR, **GATEWAY, "port": 8089, "description": "TAKX to TAK streaming"}])

    def test_uds_package_never_opens_wide(self):
        for documents in (self.default, self.full):
            for rule in one(documents, "Package")["spec"]["network"].get("allow", []):
                self.assertEqual(rule["direction"], "Egress")
                self.assertEqual(rule["selector"], SELECTOR)
                for key in ("remoteGenerated", "remoteCidr", "ports"):
                    self.assertNotIn(key, rule)
                self.assertEqual(sum(key in rule for key in ("remoteNamespace", "remoteHost")), 1)

    def test_every_branch(self):
        package = one(self.full, "Package")
        self.assertEqual(package["spec"]["sso"][0]["redirectUris"], ["https://takx-two.example.test/login"])
        self.assertEqual(package["spec"]["network"]["expose"][0]["host"], "takx-two")
        self.assertEqual(package["spec"]["network"]["allow"], [
            {"direction": "Egress", "selector": SELECTOR, **GATEWAY, "port": 8089, "description": "TAKX to TAK streaming"},
            {"direction": "Egress", "selector": SELECTOR, **GATEWAY, "port": 443, "description": "TAKX to TAK API by SNI"},
            {"direction": "Egress", "selector": SELECTOR, "serviceAccount": "takx-desktop",
             "remoteHost": "tiles.example.test", "remoteProtocol": "TLS", "port": 443,
             "description": "TAKX to tiles.example.test"}])
        pod_spec = one(self.full, "Deployment")["spec"]["template"]["spec"]
        self.assertEqual(pod_spec["hostAliases"], [{"ip": "10.43.0.99", "hostnames": ["takserver"]},
                                                   {"ip": "10.43.0.98", "hostnames": ["extra.example.test"]}])
        volumes = {volume["name"]: volume for volume in pod_spec["volumes"]}
        self.assertEqual(volumes["session"], {"name": "session", "persistentVolumeClaim": {"claimName": "takx-desktop-session"}})
        claim = one(self.full, "PersistentVolumeClaim")
        self.assertEqual(claim["spec"], {"accessModes": ["ReadWriteOnce"], "storageClassName": "local-path",
                                         "resources": {"requests": {"storage": "2Gi"}}})
        containers = {c["name"]: c for c in all_containers(pod_spec)}
        env = {name: {v["name"]: v["value"] for v in c.get("env", [])} for name, c in containers.items()}
        self.assertEqual(env["identity"], {"TAKX_IDENTITY_EXPECT_HOST": "takserver-stream.example.test",
                                           "TAKX_IDENTITY_CONNECT_HOST": "tak.example.test"})
        self.assertEqual(env["desktop"]["TAKX_ARGS"], "-create")

    def test_no_egress_at_all(self):
        documents = helm_template({"tak": {"egress": {"streaming": False}}})
        self.assertNotIn("allow", one(documents, "Package")["spec"]["network"])

    def test_schema_refuses_bad_values(self):
        for bad in ({"sso": {"groups": []}}, {"nope": 1}, {"display": {"depth": 8}}, {"egress": {"hosts": ["*.x.test"]}}):
            done = subprocess.run((UDS, "zarf", "tools", "helm", "template", "takx-desktop", CHART, "-f", "-"),
                                  input=json.dumps(bad).encode(), capture_output=True, check=False, cwd="/")
            self.assertNotEqual(done.returncode, 0, bad)


@need_uds
class Policies(unittest.TestCase):
    """UDS Core v1.16.0 admission policies and the repository's pod policy, per policy."""

    @classmethod
    def setUpClass(cls):
        cls.pods = {}
        for label, values in (("default", None), ("every branch", ALL_VALUES)):
            documents = helm_template(values)
            template = one(documents, "Deployment")["spec"]["template"]
            cls.pods[label] = {"metadata": {"namespace": "takx-desktop", **template["metadata"]}, "spec": template["spec"]}
            cls.service = one(documents, "Service")

    def test_each_uds_core_pod_policy_approves(self):
        for label, pod in self.pods.items():
            for policy, check in UDS_POD_POLICIES.items():
                self.assertEqual(check(pod), [], f"{policy} on the {label} pod")

    def test_each_predicate_rejects_a_violation(self):
        # Control: every predicate must fire on a pod changed to break it.
        pod = self.pods["default"]
        for policy, check in UDS_POD_POLICIES.items():
            self.assertTrue(check(_violate(pod, policy)), f"{policy} did not reject its violation")

    def test_every_container_sets_a_security_context(self):
        # The UDS Core predicates only look at containers that set one.
        for pod in self.pods.values():
            for container in all_containers(pod["spec"]):
                self.assertIn("securityContext", container, container["name"])
            self.assertEqual(len(_contexts(pod)), 3)

    def test_uds_core_service_policies_approve(self):
        self.assertEqual(restrict_external_names(self.service), [])
        self.assertEqual(disallow_node_port_services(self.service), [])

    def test_repository_pod_policy_approves(self):
        for label, pod in self.pods.items():
            self.assertIsNone(tak_pod_zero_trust(pod), label)
        bad = copy.deepcopy(self.pods["default"])
        bad["spec"]["containers"][0]["securityContext"]["capabilities"]["add"] = ["NET_BIND_SERVICE"]
        self.assertEqual(tak_pod_zero_trust(bad), "container desktop adds capabilities")

    def test_stricter_than_the_policies_ask(self):
        # Not required by a policy; asserted so a later edit cannot drop them unnoticed.
        for pod in self.pods.values():
            for container in all_containers(pod["spec"]):
                self.assertIs(container["securityContext"]["readOnlyRootFilesystem"], True)
                self.assertNotIn("add", container["securityContext"]["capabilities"])
            self.assertEqual(pod["spec"]["securityContext"]["seccompProfile"], {"type": "RuntimeDefault"})
            self.assertIs(pod["spec"]["automountServiceAccountToken"], False)
            kinds = {next(key for key in volume if key != "name") for volume in pod["spec"]["volumes"]}
            self.assertLessEqual(kinds, {"emptyDir", "secret", "persistentVolumeClaim"})

    def test_package_validator_rules(self):
        # src/pepr/operator/crd/validators/package-validator.ts, the rules a rendered Package can break.
        for values in (None, ALL_VALUES):
            package = one(helm_template(values), "Package")
            self.assertNotIn(package["metadata"]["namespace"], ("kube-system", "kube-public", "pepr-system"))
            for expose in package["spec"]["network"]["expose"]:
                self.assertTrue(expose["host"])
                self.assertNotIn("domain", expose)
                self.assertNotIn("advancedHTTP", expose)
            names = set()
            for rule in package["spec"]["network"].get("allow", []):
                remote = [key for key in ("remoteGenerated", "remoteNamespace", "remoteSelector", "remoteCidr", "remoteHost")
                          if key in rule]
                self.assertTrue(remote)
                if "remoteHost" in rule:
                    self.assertNotIn("*", rule["remoteHost"])
                    self.assertEqual(rule["direction"], "Egress")
                    self.assertIn(rule.get("remoteProtocol"), ("TLS", "HTTP"))
                    self.assertFalse({"remoteNamespace", "remoteSelector", "remoteGenerated", "remoteCidr"} & set(rule))
                else:
                    # serviceAccount is only valid with remoteHost or Anywhere egress.
                    self.assertNotIn("serviceAccount", rule)
                    self.assertNotIn(rule.get("remoteProtocol"), ("TLS", "HTTP"))
                name = json.dumps({key: value for key, value in rule.items() if key != "description"}, sort_keys=True)
                self.assertNotIn(name, names)
                names.add(name)
            descriptions = [rule["description"] for rule in package["spec"]["network"].get("allow", [])]
            self.assertEqual(len(descriptions), len(set(descriptions)))
            ids = [client["clientId"] for client in package["spec"]["sso"]]
            self.assertEqual(len(ids), len(set(ids)))
            for client in package["spec"]["sso"]:
                self.assertNotIn(":", client["clientId"])
                self.assertTrue(client["redirectUris"])
                self.assertTrue(client["groups"]["anyOf"])


@need_uds
class Bundle(unittest.TestCase):
    """bundle/uds-bundle.yaml against the package and the chart it names."""

    @classmethod
    def setUpClass(cls):
        (cls.bundle,) = load_yaml(os.path.join(BUNDLE, "uds-bundle.yaml"))
        cls.package = load_yaml(os.path.join(HERE, "zarf.yaml"))[0]
        (cls.values,) = load_yaml(os.path.join(CHART, "values.yaml"))

    def test_packages(self):
        self.assertEqual(self.bundle["kind"], "UDSBundle")
        self.assertEqual(self.bundle["metadata"]["name"], "takx-desktop-proto")
        names = [package["name"] for package in self.bundle["packages"]]
        self.assertEqual(names, ["init", "core-base", "core-identity-authorization", "takserver", "takx-desktop"])
        for package in self.bundle["packages"][:-1]:
            self.assertTrue(package["repository"].startswith("ghcr.io/"))
            self.assertNotIn("path", package)
            self.assertNotIn("latest", package["ref"])
        for name in ("init", "core-base", "core-identity-authorization", "takserver"):
            package = next(p for p in self.bundle["packages"] if p["name"] == name)
            self.assertEqual(set(package["keylessVerification"]), {"certificateIdentityRegexp", "certificateOIDCIssuer"})
        core = next(p for p in self.bundle["packages"] if p["name"] == "core-base")
        self.assertIn("istio-passthrough-gateway", core["optionalComponents"])
        ports = next(value["value"] for value in core["overrides"]["istio-passthrough-gateway"]["gateway"]["values"]
                     if value["path"] == "service.ports")
        self.assertIn(8089, [port["port"] for port in ports])

    def test_core_block_matches_the_repository_bundle(self):
        (full,) = load_yaml(os.path.join(REPO, "bundle", "full", "uds-bundle.yaml"))
        theirs = next(p for p in full["packages"] if p["name"] == "core-base")
        ours = next(p for p in self.bundle["packages"] if p["name"] == "core-base")
        self.assertEqual(ours["ref"], theirs["ref"])
        self.assertEqual(ours["keylessVerification"], theirs["keylessVerification"])
        for component in ("pepr-uds-core", "istio-controlplane", "istio-passthrough-gateway"):
            self.assertEqual(ours["overrides"][component], theirs["overrides"][component], component)

    def test_takx_desktop_entry(self):
        entry = self.bundle["packages"][-1]
        self.assertEqual(entry["path"], "takx-package-dir")
        self.assertEqual(entry["ref"], "takx-build-tag")
        self.assertNotIn("repository", entry)
        optional = {c["name"] for c in self.package["components"] if c.get("optional")}
        self.assertEqual(set(entry["optionalComponents"]), optional)
        self.assertEqual(list(entry["overrides"]), ["takx-desktop"])
        self.assertEqual(list(entry["overrides"]["takx-desktop"]), ["takx-desktop"])
        chart = entry["overrides"]["takx-desktop"]["takx-desktop"]
        self.assertEqual(set(chart), {"variables"})
        names = [variable["name"] for variable in chart["variables"]]
        self.assertEqual(len(names), len(set(names)))
        for variable in chart["variables"]:
            node = self.values
            for part in variable["path"].split("."):
                self.assertIn(part, node, variable["path"])
                node = node[part]
            # Only string values: a bundle variable arrives as a string.
            self.assertIsInstance(node, str, variable["path"])
            self.assertNotIn("sensitive", variable)
            self.assertNotEqual(variable["path"], "image")

    def test_test_values_path_exists(self):
        takserver = next(p for p in self.bundle["packages"] if p["name"] == "takserver")
        (path,) = takserver["overrides"]["takserver"]["takserver"]["valuesFiles"]
        self.assertTrue(os.path.isfile(os.path.join(BUNDLE, path)), path)

    def test_config_has_no_secret(self):
        (config,) = load_yaml(os.path.join(BUNDLE, "uds-config.yaml"))
        self.assertEqual(config, {"shared": {"DOMAIN": "uds.dev"}})


class Text(unittest.TestCase):
    """Labels, headers and what must never be in a tracked file. Needs no tool."""

    @classmethod
    def setUpClass(cls):
        cls.files = [os.path.join(PROTO, "stages", "package.sh")]
        for top in ("zarf", "chart", "bundle"):
            for folder, _dirs, names in os.walk(os.path.join(PROTO, top)):
                cls.files += [os.path.join(folder, name) for name in names if "__pycache__" not in folder]
        cls.text = {}
        for path in cls.files:
            with open(path, encoding="utf-8") as stream:
                cls.text[path] = stream.read()

    def test_headers(self):
        for path in self.files:
            if path.endswith(".md"):
                self.assertTrue(self.text[path].startswith(
                    "<!-- Copyright 2026 uds-takserver authors -->\n<!-- SPDX-License-Identifier: GPL-3.0-only -->\n"), path)
            else:
                self.assertIsNone(header_problem(path), path)

    def test_honest_labels(self):
        for path in (os.path.join(HERE, "zarf.yaml"), os.path.join(CHART, "Chart.yaml"), os.path.join(HERE, "README.md"),
                     os.path.join(BUNDLE, "uds-bundle.yaml"), os.path.join(PROTO, "stages", "package.sh")):
            flat = " ".join(self.text[path].replace("#", " ").split())
            self.assertIn("Kernel 8.30.5 + project patches", flat, path)
            self.assertIn("CommonCommo 1.17.2 patched", flat, path)
            self.assertIn("not the official takx", flat.lower(), path)
        # What TAKX 5.7 declares (../README.md) is not what is inside, and no file here may say so.
        private = ("8.57.11", "1.20.0", "2.2.1-" + "TAKX", "9.12.6")
        for path, text in self.text.items():
            if os.path.basename(path) == "test_package.py":
                continue
            for version in private:
                self.assertNotIn(version, text, path)

    def test_no_key_material_or_credentials(self):
        markers = ("BEGIN CERTIFICATE", "PRIVATE KEY", "BEGIN PKCS", "stringData")
        # A key named like a credential with a value, in YAML, JSON or shell.
        assigned = re.compile(r"""(?im)^\s*(-\s*)?(export\s+)?["']?\w*(password|passwd|token|apikey|api_key)\w*["']?\s*[:=]\s*["']?[^\s"'{$]""")
        for path, text in self.text.items():
            if os.path.basename(path) == "test_package.py":
                continue
            for marker in markers:
                self.assertNotIn(marker, text, f"{marker} in {path}")
            self.assertIsNone(assigned.search(text.replace("automountServiceAccountToken", "automount")), path)
        self.assertIsNotNone(assigned.search("  adminPassword: hunter2\n"))
        self.assertIsNotNone(assigned.search("export API_TOKEN=abc\n"))

    def test_chart_templates_opt_out_of_yamllint(self):
        # yamllint cannot read Helm templates, and the repository's ignore
        # (charts/*/templates/**) does not reach this directory.
        folder = os.path.join(CHART, "templates")
        for name in sorted(os.listdir(folder)):
            if name.endswith(".yaml"):
                self.assertTrue(self.text[os.path.join(folder, name)].startswith("# yamllint disable-file\n"), name)

    def test_schema_checker_itself(self):
        schema = {"type": "object", "additionalProperties": False, "required": ["a"],
                  "properties": {"a": {"type": "string", "pattern": "^x"}, "b": {"enum": [1, 2]}}}
        self.assertEqual(schema_errors({"a": "xy", "b": 2}, schema), [])
        self.assertEqual(len(schema_errors({"a": "y", "b": 3, "c": 0}, schema)), 3)
        self.assertEqual(len(schema_errors({}, schema)), 1)
        with self.assertRaises(NotImplementedError):
            schema_errors({}, {"if": {}})

    def test_crd_conversion_is_strict(self):
        schema = crd_schema({"type": "object", "properties": {
            "spec": {"type": "object", "properties": {"port": {"x-kubernetes-int-or-string": True}}},
            "free": {"type": "object", "x-kubernetes-preserve-unknown-fields": True, "properties": {"a": {"type": "string"}}}}})
        self.assertIs(schema["additionalProperties"], False)
        self.assertIs(schema["properties"]["spec"]["additionalProperties"], False)
        self.assertNotIn("additionalProperties", schema["properties"]["free"])
        self.assertEqual(schema_errors({"spec": {"port": "http"}}, schema), [])
        self.assertTrue(schema_errors({"spec": {"prot": 1}}, schema))


# --- script modes ------------------------------------------------------------


def main_headers(files):
    bad = 0
    for path in files:
        if path.endswith(".md"):
            with open(path, encoding="utf-8") as stream:
                problem = None if stream.read(200).startswith(
                    "<!-- Copyright 2026 uds-takserver authors -->\n<!-- SPDX-License-Identifier: GPL-3.0-only -->\n") \
                    else "no header"
        else:
            problem = header_problem(path)
        if problem:
            print(f"{path}: {problem}")
            bad += 1
    print(f"licence header: {len(files) - bad} of {len(files)} files ok")
    return 1 if bad else 0


def main_action_scripts(component_file, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(component_file))[0]
    for name in os.listdir(out_dir):
        if name.startswith(stem + "."):
            os.unlink(os.path.join(out_dir, name))
    commands = action_commands(component_file)
    for path, shell, cmd, _action in commands:
        # As lint:shell of uds-common does: templates become a plain word.
        script = f"#!/bin/{shell}\n" + re.sub(r"\{\{[^}]*\}\}", "zarf-expression", cmd).rstrip("\n") + "\n"
        with open(os.path.join(out_dir, f"{stem}.{path}.sh"), "w", encoding="utf-8") as stream:
            stream.write(script)
    print(f"{component_file}: {len(commands)} action commands")
    return 0


def main_bundle_schema(schema_file, files):
    with open(schema_file, encoding="utf-8") as stream:
        schema = json.load(stream)
    bad = 0
    for path in files:
        (bundle,) = load_yaml(path)
        errors = schema_errors(bundle, schema)
        print(f"{path}: {len(bundle['packages'])} packages, {len(errors)} schema errors")
        for error in errors:
            print(f"  {error}")
        bad += len(errors)
    return 1 if bad else 0


def main_built(inspect_dir, tag):
    """Assert what `zarf package inspect` extracted from the built package."""
    attachments = os.path.join(inspect_dir, "attachments")
    found = {}
    for folder, _dirs, names in os.walk(attachments):
        for name in names:
            found[name] = os.path.join(folder, name)
    wanted = ("README.md", "NOTICE.md", "SOURCES.json", "LICENCE-GATE.json", "LICENSES.tar", "PATCHES.tar",
              "DEBIAN-PACKAGES.tsv")
    missing = [name for name in wanted if name not in found]
    if missing:
        print(f"attachments missing: {missing}; found {sorted(found)}")
        return 1
    with open(found["SOURCES.json"], encoding="utf-8") as stream:
        sources = json.load(stream)
    digest = sources["tree"]["manifest_sha256"]
    with tarfile.open(found["LICENSES.tar"]) as archive:
        licences = [member.name for member in archive.getmembers() if member.isfile()]
    with tarfile.open(found["PATCHES.tar"]) as archive:
        patches = [member.name for member in archive.getmembers() if member.isfile()]
    with open(found["LICENCE-GATE.json"], encoding="utf-8") as stream:
        gate = json.load(stream)
    with open(found["NOTICE.md"], encoding="utf-8") as stream:
        notice = stream.read()
    with open(found["DEBIAN-PACKAGES.tsv"], encoding="utf-8") as stream:
        packages = sum(1 for line in stream if line.strip() and not line.startswith("#"))
    sbom = []
    for folder, _dirs, names in os.walk(os.path.join(inspect_dir, "sbom")):
        sbom += [os.path.join(folder, name) for name in names]
    kinds = {}
    for path in sbom:
        if path.endswith(".json"):
            with open(path, encoding="utf-8") as stream:
                for artifact in json.load(stream).get("artifacts", []):
                    kinds[artifact.get("type")] = kinds.get(artifact.get("type"), 0) + 1
    checks = {
        "SBOM lists Debian packages and Java archives": kinds.get("deb", 0) > 100 and kinds.get("java-archive", 0) > 100,
        "tag ends in the tree digest of SOURCES.json": tag.endswith("-" + digest[:12]),
        "SOURCES.json says publishable false": sources.get("publishable") is False,
        "LICENCE-GATE.json: nothing denied or unknown": gate["build_may_proceed"] is True
        and gate["counts"]["deny"] == 0 and gate["counts"]["unknown"] == 0,
        "LICENCE-GATE.json and SOURCES.json agree, and publishable is false while anything is flagged":
            gate["counts"] == sources["licence_gate"]["counts"] and (gate["clean"] or sources.get("publishable") is False),
        "LICENSES.tar holds the text of every licence in use": all(
            "LICENSES/texts/%s.txt" % name.replace(" ", "_") in licences for name in gate["licences_in_use"]),
        "no FFmpeg native JAR, wizard reference JAR, joost or JOCL in the tree": not [
            item["path"] for item in sources["files"]
            if re.search(r"ffmpeg-[^/]*linux[^/]*\.jar$|wizard-reference|joost|jocl", item["path"])],
        "SOURCES.json maps every tree file": sources["counts"]["files"] == sources["tree"]["files"] == len(sources["files"]),
        "LICENSES.tar holds GPL-3.0-only.txt": "LICENSES/GPL-3.0-only.txt" in licences,
        "LICENSES.tar holds more than 100 files": len(licences) > 100,
        "PATCHES.tar holds patches and lock files": any(n.startswith("patches/") for n in patches)
        and any(n.startswith("locks/") for n in patches),
        "NOTICE.md names the substitutions": "8.30.5" in notice and "1.17.2" in notice,
        "DEBIAN-PACKAGES.tsv lists packages": packages > 100,
        "SBOM files for the image": any(os.path.getsize(path) > 10000 for path in sbom),
    }
    for label, ok in checks.items():
        print(f"{'ok  ' if ok else 'FAIL'} {label}")
    print(f"attachments {sorted(found)}; licence files {len(licences)}; patch and lock files {len(patches)}; "
          f"Debian package rows {packages}; SBOM files {len(sbom)}, artifacts {dict(sorted(kinds.items()))}; tree {digest}; "
          f"publishable blockers {len(sources.get('publishable_blockers', []))}")
    return 0 if all(checks.values()) else 1


def archive_facts(directory, image_id):
    """Checks on a decompressed package: (checks, facts).

    Zarf 1.0.1 writes two documents into the zarf.yaml of a built package:
    first its own conversion to zarf.dev/v1alpha1, which is what a CLI
    older than v1beta1 reads, then the zarf.dev/v1beta1 definition that
    `zarf package inspect definition` prints. Both must name the image by
    the digest of the image that was built, and the OCI layout of the
    package must hold exactly that manifest."""
    documents = load_yaml(os.path.join(directory, "zarf.yaml"))
    versions = [document.get("apiVersion") for document in documents]
    references = []
    for document in documents:
        for component in document.get("components", []):
            for image in component.get("images", []):
                references.append(image["name"] if isinstance(image, dict) else image)
    with open(os.path.join(directory, "images", "index.json"), encoding="utf-8") as stream:
        manifests = json.load(stream)["manifests"]
    pinned = re.compile(re.escape(IMAGE) + r":[A-Za-z0-9_.-]+@" + re.escape(image_id) + "$")
    blob = os.path.join(directory, "images", "blobs", "sha256", image_id.split(":", 1)[-1])
    checks = {
        "zarf.yaml holds the v1alpha1 conversion and then the v1beta1 definition":
            versions == ["zarf.dev/v1alpha1", "zarf.dev/v1beta1"],
        "every document names the one image by tag and by the digest of the built image":
            len(references) == len(documents) and all(pinned.match(reference) for reference in references),
        "the OCI layout holds one manifest, with that digest": [m["digest"] for m in manifests] == [image_id],
        "the manifest blob is in the package": os.path.isfile(blob),
    }
    return checks, {"apiVersions": versions, "image_references": sorted(set(references)),
                    "oci_manifests": [m["digest"] for m in manifests]}


def main_archive(directory, image_id):
    checks, facts = archive_facts(directory, image_id)
    for label, ok in checks.items():
        print(f"{'ok  ' if ok else 'FAIL'} {label}")
    print(json.dumps(facts))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--archive":
        sys.exit(main_archive(sys.argv[2], sys.argv[3]))
    if len(sys.argv) > 1 and sys.argv[1] == "--headers":
        sys.exit(main_headers(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "--action-scripts":
        sys.exit(main_action_scripts(sys.argv[2], sys.argv[3]))
    if len(sys.argv) > 1 and sys.argv[1] == "--crd-schemas":
        sys.exit(write_crd_schemas(sys.argv[2], sys.argv[3:]) or 0)
    if len(sys.argv) > 1 and sys.argv[1] == "--bundle-schema":
        sys.exit(main_bundle_schema(sys.argv[2], sys.argv[3:]))
    if len(sys.argv) > 1 and sys.argv[1] == "--built":
        sys.exit(main_built(sys.argv[2], sys.argv[3]))
    unittest.main()
