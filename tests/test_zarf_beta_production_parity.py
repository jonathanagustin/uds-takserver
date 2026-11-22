# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Production v1alpha1 vs staged v1beta1 render parity guard for #712.

Stages a temporary coordinated zarf.dev/v1beta1 copy of the real root and
common packages (with concrete package-value defaults and no legacy variables),
then verifies resource-identity and override parity against the production
v1alpha1 render via `uds zarf dev inspect manifests`.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from tests import test_package_values as pv

ROOT = Path(__file__).resolve().parents[1]


def _convert_chart(chart):
    """Convert one v1alpha1 Zarf chart entry to zarf.dev/v1beta1."""
    out = {"name": chart["name"]}
    for key in ("namespace", "releaseName", "serverSideApply"):
        if key in chart:
            out[key] = chart[key]
    if "localPath" in chart:
        out["local"] = {"path": chart["localPath"]}
    elif "url" in chart and "gitPath" in chart:
        out["git"] = {
            "url": chart["url"],
            "path": chart["gitPath"],
            "ref": {"tag": str(chart["version"])},
        }
    files = [{"path": p} for p in chart.get("valuesFiles") or ()]
    files += [{"path": p, "enableTemplating": True} for p in chart.get("templatedValuesFiles") or ()]
    if files:
        out["valuesFiles"] = files
    if "values" in chart:
        out["values"] = chart["values"]
    return out


def _convert_component(component):
    """Convert one v1alpha1 Zarf component entry to zarf.dev/v1beta1."""
    out = {"name": component["name"]}
    if "description" in component:
        out["description"] = component["description"]
    if component.get("required") is False:
        out["optional"] = True
    if "only" in component:
        out["selector"] = component["only"]
    if "import" in component:
        imp_path = component["import"]["path"].rstrip("/")
        out["import"] = {"local": [{"path": f"{imp_path}/zarf.yaml"}]}
    if "charts" in component:
        out["charts"] = [_convert_chart(c) for c in component["charts"]]
    if "images" in component:
        out["images"] = [{"name": img} for img in component["images"]]
    actions = {}
    if "actions" in component:
        actions = {k: dict(v) for k, v in component["actions"].items()}
    if "healthChecks" in component:
        on_deploy = dict(actions.get("onDeploy") or {})
        waits = list(on_deploy.get("onSuccess") or [])
        for hc in component["healthChecks"]:
            kind = hc["kind"].lower()
            condition = "available" if kind == "deployment" else "exists"
            waits.append({
                "description": f"Wait for {hc['kind']}/{hc['name']}",
                "wait": {
                    "cluster": {
                        "kind": kind,
                        "name": hc["name"],
                        "namespace": hc["namespace"],
                        "condition": condition,
                    }
                },
            })
        on_deploy["onSuccess"] = waits
        actions["onDeploy"] = on_deploy
    if actions:
        out["actions"] = actions
    return out


def _replace_placeholders(obj, defaults):
    """Replace ###ZARF_VAR_<NAME>### placeholders with concrete variable defaults."""
    if isinstance(obj, dict):
        return {k: _replace_placeholders(v, defaults) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_placeholders(v, defaults) for v in obj]
    if isinstance(obj, str) and obj.startswith("###ZARF_VAR_") and obj.endswith("###"):
        var_name = obj[len("###ZARF_VAR_"):-len("###")]
        if var_name not in defaults:
            raise KeyError(f"unknown Zarf variable placeholder: {var_name}")
        return defaults[var_name]
    return obj


def stage_beta_tree(dest):
    """Build a temporary coordinated v1beta1 staging copy of the production package."""
    dest = Path(dest)
    shutil.copytree(ROOT / "values", dest / "values")
    for link in ("charts", "pepr"):
        os.symlink(ROOT / link, dest / link)

    root_alpha = yaml.safe_load((ROOT / "zarf.yaml").read_text())
    common_alpha = yaml.safe_load((ROOT / "common/zarf.yaml").read_text())
    defaults = {v["name"]: v.get("default", "") for v in root_alpha["variables"]}

    values_path = dest / "values/zarf-values.yaml"
    values_beta = _replace_placeholders(yaml.safe_load(values_path.read_text()), defaults)
    values_path.write_text(yaml.safe_dump(values_beta, sort_keys=False))

    root_beta = {
        "apiVersion": "zarf.dev/v1beta1",
        "kind": "ZarfPackageConfig",
        "metadata": root_alpha["metadata"],
        "values": root_alpha["values"],
        "components": [_convert_component(c) for c in root_alpha["components"]],
    }
    common_comp = _convert_component(common_alpha["components"][0])
    common_comp.pop("name", None)
    common_meta = dict(common_alpha["metadata"])
    common_meta.setdefault("version", root_alpha["metadata"].get("version", "dev"))
    common_beta = {
        "apiVersion": "zarf.dev/v1beta1",
        "kind": "ZarfComponentConfig",
        "metadata": common_meta,
        "component": common_comp,
    }
    (dest / "common").mkdir()
    (dest / "common/zarf.yaml").write_text(yaml.safe_dump(common_beta, sort_keys=False))
    (dest / "zarf.yaml").write_text(yaml.safe_dump(root_beta, sort_keys=False))
    return root_beta, common_beta, values_beta


def _render_dir(directory, variables=(), values=()):
    cmd = [
        "uds", "zarf", "dev", "inspect", "manifests", ".", "--flavor", "upstream",
        "--no-color", "--log-level", "warn",
    ]
    for pair in variables:
        cmd += ["--deploy-set-variables", pair]
    for pair in values:
        cmd += ["--set-values", pair]
    result = subprocess.run(cmd, cwd=directory, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(pv.render_failure(result))
    out = result.stdout
    docs = [doc for doc in yaml.safe_load_all(out) if isinstance(doc, dict)]
    return out, docs


def _identities(docs):
    return sorted(
        (doc.get("kind"), (doc.get("metadata") or {}).get("namespace", ""), (doc.get("metadata") or {}).get("name", ""))
        for doc in docs
    )


class BetaStagingTransformTests(unittest.TestCase):
    """Offline checks for the coordinated v1beta1 staging transform."""

    def test_staged_beta_transform_produces_clean_v1beta1_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_beta, common_beta, values_beta = stage_beta_tree(tmp)
            self.assertEqual(root_beta["apiVersion"], "zarf.dev/v1beta1")
            self.assertEqual(common_beta["apiVersion"], "zarf.dev/v1beta1")
            self.assertNotIn("variables", root_beta)
            self.assertNotIn("###ZARF_VAR_", yaml.safe_dump(values_beta))
            by_name = {c["name"]: c for c in root_beta["components"]}
            self.assertEqual(by_name["takserver"]["import"], {"local": [{"path": "common/zarf.yaml"}]})
            self.assertEqual(by_name["takserver"]["selector"], {"flavor": "upstream"})
            self.assertTrue(by_name["cloudtak"].get("optional"))
            self.assertNotIn("required", by_name["cloudtak"])
            self.assertNotIn("default", by_name["cloudtak"])
            self.assertTrue(all(isinstance(i, dict) and "name" in i for i in by_name["takserver"]["images"]))
            self.assertNotIn("healthChecks", by_name["takserver"])
            self.assertNotIn("healthChecks", by_name["cloudtak"])
            self.assertEqual(len(by_name["takserver"]["actions"]["onDeploy"]["onSuccess"]), 2)
            self.assertEqual(len(by_name["cloudtak"]["actions"]["onDeploy"]["onSuccess"]), 2)
            self.assertEqual(len(by_name["takserver"]["actions"]["onCreate"]["before"]), 1)


@unittest.skipUnless(shutil.which("uds") or os.environ.get("CI"), "uds CLI is not installed")
class ProductionBetaRenderParityTests(unittest.TestCase):
    """Real CLI parity checks between production v1alpha1 and staged v1beta1."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.beta_dir = Path(cls._tmp.name)
        stage_beta_tree(cls.beta_dir)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_default_render_matches_production_v1alpha1_identities(self):
        _alpha_out, alpha_docs = pv.render()
        beta_out, beta_docs = _render_dir(self.beta_dir)
        self.assertNotIn("###ZARF_VAR_", beta_out)
        self.assertEqual(_identities(beta_docs), _identities(alpha_docs))
        self.assertEqual(pv.tak_tags(beta_docs), {"5.7-RELEASE-14"})
        self.assertEqual(pv.watcher_env(beta_docs)["DOMAIN"], "uds.dev")

    def test_local_backup_and_all_overrides_match_production_v1alpha1(self):
        alpha_vars = [
            "DOMAIN=beta-parity.example",
            "TAK_IMAGE_TAG=8.8-RELEASE-8",
            "IN_CLUSTER_DNS=false",
            "PASSTHROUGH_HOSTS=takserver-api",
            "PLUGINS_MEMORY_REQUEST=384Mi",
            "PLUGINS_MEMORY_LIMIT=768Mi",
            "FILESYSTEM_BACKUP=true",
            "FILESYSTEM_BACKUP_OWNER=local",
            "POSTGRES_STORAGE_CLASS=pg-beta",
            "CLOUDTAK_STORAGE_CLASS=ct-beta",
        ]
        beta_vals = [
            "global.domain=beta-parity.example",
            "tak.imageTag=8.8-RELEASE-8",
            "dns.inCluster=false",
            "dns.passthroughHosts=takserver-api",
            "tak.plugins.memoryRequest=384Mi",
            "tak.plugins.memoryLimit=768Mi",
            "backup.filesystem.enabled=true",
            "backup.filesystem.owner=local",
            "storage.postgresClass=pg-beta",
            "storage.cloudtakClass=ct-beta",
        ]
        _alpha_out, alpha_docs = pv.render(variables=alpha_vars)
        beta_out, beta_docs = _render_dir(self.beta_dir, values=beta_vals)
        self.assertNotIn("###ZARF_VAR_", beta_out)
        self.assertEqual(_identities(beta_docs), _identities(alpha_docs))
        self.assertEqual(pv.gateway_hosts(beta_docs), pv.gateway_hosts(alpha_docs))
        self.assertEqual(pv.watcher_env(beta_docs), pv.watcher_env(alpha_docs))
        self.assertEqual(pv.admission_env(beta_docs), pv.admission_env(alpha_docs))
        self.assertEqual(pv.tak_tags(beta_docs), {"8.8-RELEASE-8"})
        self.assertTrue(
            pv.find(beta_docs, "Job", "takserver-cert-init")["spec"]["template"]["spec"]["containers"][0]["image"].endswith(":8.8-RELEASE-8")
        )
        self.assertTrue(
            pv.find(beta_docs, "Deployment", "tak-health")["spec"]["template"]["spec"]["containers"][0]["image"].endswith(":8.8-RELEASE-8")
        )
        self.assertEqual(
            pv.find(beta_docs, "StatefulSet", "takserver-postgresql")["spec"]["volumeClaimTemplates"][0]["spec"]["storageClassName"],
            "pg-beta",
        )
        self.assertEqual(
            pv.find(beta_docs, "StatefulSet", "cloudtak-postgis")["spec"]["volumeClaimTemplates"][0]["spec"]["storageClassName"],
            "ct-beta",
        )
        # Local owner renders the Velero node-agent DaemonSet.
        self.assertIsNotNone(pv.find(beta_docs, "DaemonSet", "node-agent"))

    def test_core_backup_owner_omits_local_node_agent_in_v1beta1(self):
        beta_out, beta_docs = _render_dir(
            self.beta_dir,
            values=["backup.filesystem.enabled=true", "backup.filesystem.owner=core"],
        )
        self.assertNotIn("###ZARF_VAR_", beta_out)
        daemonsets = [d for d in beta_docs if d.get("kind") == "DaemonSet" and (d.get("metadata") or {}).get("name") == "node-agent"]
        self.assertEqual(daemonsets, [])
        tak_ann = pv.find(beta_docs, "StatefulSet", "takserver-postgresql")["spec"]["template"]["metadata"]["annotations"]
        cloud_ann = pv.find(beta_docs, "StatefulSet", "cloudtak-postgis")["spec"]["template"]["metadata"]["annotations"]
        self.assertEqual(tak_ann.get("backup.velero.io/backup-volumes"), "data")
        self.assertEqual(cloud_ann.get("backup.velero.io/backup-volumes"), "data")

    def test_chart_overrides_and_health_disabled_match_production_v1alpha1(self):
        vals = [
            "health.enabled=false",
            "tak.imageTag=8.8-RELEASE-8",
            "chartOverrides.takserver.uds-takserver-config.backup.dump.enabled=true",
            "chartOverrides.cloudtak.uds-cloudtak.backup.dump.enabled=true",
        ]
        _alpha_out, alpha_docs = pv.render(values=vals)
        beta_out, beta_docs = _render_dir(self.beta_dir, values=vals)
        self.assertNotIn("###ZARF_VAR_", beta_out)
        self.assertEqual(_identities(beta_docs), _identities(alpha_docs))
        names = {(d.get("kind"), (d.get("metadata") or {}).get("name")) for d in beta_docs}
        self.assertNotIn(("Deployment", "tak-health"), names)
        self.assertIn(("CronJob", "tak-db-dump"), names)
        self.assertIn(("CronJob", "cloudtak-db-dump"), names)
        tak_dump = pv.find(beta_docs, "CronJob", "tak-db-dump")["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
        cloud_dump = pv.find(beta_docs, "CronJob", "cloudtak-db-dump")["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
        self.assertTrue(tak_dump["image"].endswith(":8.8-RELEASE-8"))
        self.assertTrue(cloud_dump["image"].endswith(":8.8-RELEASE-8"))

    def test_values_schema_rejects_invalid_backup_owner_in_v1beta1(self):
        result = subprocess.run(
            [
                "uds", "zarf", "dev", "inspect", "manifests", ".", "--flavor", "upstream",
                "--no-color", "--log-level", "warn",
                "--set-values", "backup.filesystem.owner=invalid",
            ],
            cwd=self.beta_dir, check=False, capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_staged_beta_package_passes_zarf_dev_lint(self):
        result = subprocess.run(
            [
                "uds", "zarf", "dev", "lint", ".", "--flavor", "upstream",
                "--no-color", "--log-level", "warn",
            ],
            cwd=self.beta_dir, check=False, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, pv.render_failure(result))
        combined = f"{result.stdout}\n{result.stderr}"
        self.assertNotIn("v1alpha1", combined)


if __name__ == "__main__":
    unittest.main()
