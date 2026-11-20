// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only

import assert from "node:assert/strict";
import { test } from "node:test";
import {
  addRender,
  CERT_SECRET,
  CERT_VOLUME,
  CONFIG_MOUNT,
  INIT_NAME,
  isTakPod,
  METRICS_PORT,
  METRICS_PORT_NAME,
  RELOAD_LABEL,
  TAK_APPS,
  type PodLike,
} from "./oidc-render.ts";

const IMAGE = "registry.example/takserver-config:1";

function pod(app: string): PodLike {
  return {
    metadata: { labels: { app } },
    spec: {
      initContainers: [{ name: "wait-deps" }],
      containers: [{ name: "main" }],
    },
  };
}

test("every TAK app gets the init container, the volumes and the mount", () => {
  for (const app of TAK_APPS) {
    const p = pod(app);
    assert.equal(addRender(p, IMAGE), true, app);
    const inits = p.spec?.initContainers ?? [];
    assert.deepEqual(
      inits.map(c => c.name),
      ["wait-deps", INIT_NAME],
      `${app}: wait-deps stays first`,
    );
    assert.equal(inits[1].image, IMAGE);
    const names = (p.spec?.volumes ?? []).map(v => v.name);
    for (const name of [CONFIG_MOUNT, "oidc-script", "oidc-template", "oidc-secret", "keystore-secret"]) {
      assert.ok(names.includes(name), `${app}: volume ${name}`);
    }
    const mounts = p.spec?.containers?.[0].volumeMounts ?? [];
    assert.ok(mounts.some(m => m.name === CONFIG_MOUNT && m.mountPath === "/conf"));
    assert.equal(p.metadata?.labels?.[RELOAD_LABEL], "takserver");
  }
});

test("plugins is covered, since it also reads CoreConfig.xml", () => {
  assert.ok(isTakPod(pod("takserver-plugins")));
});

test("pods that do not read CoreConfig.xml are left alone", () => {
  for (const app of ["takserver-ignite", "takserver-nats", "takserver-postgresql"]) {
    const p = pod(app);
    assert.equal(addRender(p, IMAGE), false, app);
    assert.deepEqual(p, pod(app), app);
  }
  assert.equal(addRender({}, IMAGE), false);
});

test("a second call changes nothing", () => {
  const p = pod("takserver-api");
  addRender(p, IMAGE);
  const once = structuredClone(p);
  assert.equal(addRender(p, IMAGE), false);
  assert.deepEqual(p, once);
});

test("api and messaging get a metrics port and the other TAK pods do not", () => {
  for (const app of ["takserver-api", "takserver-messaging"]) {
    const p = pod(app);
    addRender(p, IMAGE);
    assert.deepEqual(p.spec?.containers?.[0].ports, [
      { name: METRICS_PORT_NAME, containerPort: METRICS_PORT, protocol: "TCP" },
    ]);
  }
  for (const app of ["takserver-config", "takserver-plugins"]) {
    const p = pod(app);
    addRender(p, IMAGE);
    assert.equal(p.spec?.containers?.[0].ports, undefined, app);
  }
});

test("an existing metrics port is not duplicated", () => {
  const p = pod("takserver-api");
  p.spec!.containers![0].ports = [{ name: METRICS_PORT_NAME, containerPort: METRICS_PORT, protocol: "TCP" }];
  addRender(p, IMAGE);
  const ports = p.spec?.containers?.[0].ports ?? [];
  assert.equal(ports.filter(port => port.name === METRICS_PORT_NAME).length, 1);
});

test("the metrics port is added beside an existing connector port", () => {
  const p = pod("takserver-messaging");
  p.spec!.containers![0].ports = [{ name: "https", containerPort: 8443, protocol: "TCP" }];
  addRender(p, IMAGE);
  assert.deepEqual(
    (p.spec?.containers?.[0].ports ?? []).map(port => port.name),
    ["https", METRICS_PORT_NAME],
  );
});

test("an existing oidc-script volume is not duplicated", () => {
  const p = pod("takserver-plugins");
  p.spec!.volumes = [{ name: "oidc-script", configMap: { name: "takserver-oidc-render" } }];
  addRender(p, IMAGE);
  const count = (p.spec?.volumes ?? []).filter(v => v.name === "oidc-script").length;
  assert.equal(count, 1);
});

function certVolume(app = "takserver-api"): PodLike {
  const p = pod(app);
  p.spec!.volumes = [
    { name: "other", emptyDir: {} },
    { name: CERT_VOLUME, configMap: { name: "cert-migration" } },
  ];
  return p;
}

test("the upstream cert volume is mounted from the Secret", () => {
  const p = certVolume();
  assert.equal(addRender(p, IMAGE), true);
  const volume = p.spec?.volumes?.find(v => v.name === CERT_VOLUME);
  assert.deepEqual(volume?.secret, { secretName: CERT_SECRET });
  assert.equal(volume?.configMap, undefined);
  assert.ok(p.spec?.volumes?.some(v => v.name === "other"));
});

test("a cert volume that already names the Secret is left alone", () => {
  const p = certVolume();
  addRender(p, IMAGE);
  const once = structuredClone(p);
  assert.equal(addRender(p, IMAGE), false);
  assert.deepEqual(p, once);
});

test("a cert volume is replaced when it still has a ConfigMap or another Secret", () => {
  const leftover = certVolume("takserver-messaging");
  leftover.spec!.volumes = [
    { name: CERT_VOLUME, secret: { secretName: CERT_SECRET }, configMap: { name: "cert-migration" } },
  ];
  assert.equal(addRender(leftover, IMAGE), true);
  const swapped = leftover.spec?.volumes?.find(v => v.name === CERT_VOLUME);
  assert.equal(swapped?.configMap, undefined);
  assert.deepEqual(swapped?.secret, { secretName: CERT_SECRET });

  const other = certVolume("takserver-config");
  other.spec!.volumes = [{ name: CERT_VOLUME, secret: { secretName: "cert-migration" } }];
  assert.equal(addRender(other, IMAGE), true);
  assert.deepEqual(other.spec?.volumes?.find(v => v.name === CERT_VOLUME)?.secret, { secretName: CERT_SECRET });
});

test("render volumes, init container spec, and CRL mount match exact contracts", () => {
  const p = pod("takserver-api");
  assert.equal(addRender(p, IMAGE), true);
  assert.deepEqual(p.spec?.volumes, [
    { name: CONFIG_MOUNT, emptyDir: {} },
    { name: "oidc-script", configMap: { name: "takserver-oidc-render" } },
    { name: "oidc-template", configMap: { name: "core-config" } },
    { name: "oidc-secret", secret: { secretName: "takserver-sso", optional: true } },
    { name: "keystore-secret", secret: { secretName: "takserver-keystore" } },
    { name: "takserver-crl", secret: { secretName: "takserver-crl", items: [{ key: "ca.crl", path: "ca.crl" }] } },
  ]);
  assert.deepEqual(p.spec?.initContainers?.[1], {
    name: INIT_NAME,
    image: IMAGE,
    imagePullPolicy: "IfNotPresent",
    command: ["bash", "/scripts/render-oidc.sh"],
    volumeMounts: [
      { name: "oidc-script", mountPath: "/scripts", readOnly: true },
      { name: "oidc-template", mountPath: "/template", readOnly: true },
      { name: "oidc-secret", mountPath: "/var/run/tak-sso", readOnly: true },
      { name: "keystore-secret", mountPath: "/var/run/tak-keystore", readOnly: true },
      { name: CONFIG_MOUNT, mountPath: "/conf" },
    ],
    resources: {
      requests: { cpu: "20m", memory: "64Mi" },
      limits: { cpu: "200m", memory: "128Mi" },
    },
  });
  assert.deepEqual(p.spec?.containers?.[0].volumeMounts, [
    { name: CONFIG_MOUNT, mountPath: "/conf" },
    { name: "takserver-crl", mountPath: "/certs/crl", readOnly: true },
  ]);
});

test("addRender returns true when any single field is missing", () => {
  const full = certVolume("takserver-api");
  addRender(full, IMAGE);

  const missingInit = structuredClone(full);
  missingInit.spec!.initContainers = [{ name: "wait-deps" }];
  assert.equal(addRender(missingInit, IMAGE), true);

  const missingConf = structuredClone(full);
  missingConf.spec!.containers![0].volumeMounts = [{ name: "takserver-crl", mountPath: "/certs/crl", readOnly: true }];
  assert.equal(addRender(missingConf, IMAGE), true);

  const missingCrl = structuredClone(full);
  missingCrl.spec!.containers![0].volumeMounts = [{ name: CONFIG_MOUNT, mountPath: "/conf" }];
  assert.equal(addRender(missingCrl, IMAGE), true);

  const missingPort = structuredClone(full);
  delete missingPort.spec!.containers![0].ports;
  assert.equal(addRender(missingPort, IMAGE), true);

  const missingLabel = structuredClone(full);
  delete missingLabel.metadata!.labels![RELOAD_LABEL];
  assert.equal(addRender(missingLabel, IMAGE), true);

  const wrongCert = structuredClone(full);
  wrongCert.spec!.volumes = wrongCert.spec!.volumes!.map(v =>
    v.name === CERT_VOLUME ? { name: CERT_VOLUME, configMap: { name: "cert-migration" } } : v,
  );
  assert.equal(addRender(wrongCert, IMAGE), true);

  const missingVol = structuredClone(full);
  missingVol.spec!.volumes = missingVol.spec!.volumes!.filter(v => v.name !== "keystore-secret");
  assert.equal(addRender(missingVol, IMAGE), true);
});

test("exported names and minimal pod shapes hold their contracts", () => {
  assert.equal(CERT_VOLUME, "config");
  assert.equal(CERT_SECRET, "takserver-certs");
  assert.equal(RELOAD_LABEL, "uds.dev/oidc-reload");
  assert.equal(INIT_NAME, "render-oidc");
  assert.equal(CONFIG_MOUNT, "rendered-core");
  assert.equal(METRICS_PORT_NAME, "metrics");
  assert.equal(isTakPod({ metadata: {} }), false);

  const bare: PodLike = { metadata: { labels: { app: "takserver-config" } } };
  assert.equal(addRender(bare, IMAGE), true);
  assert.deepEqual(bare.spec?.initContainers?.map(c => c.name), ["render-oidc"]);
  assert.equal(bare.spec?.containers, undefined);
});

test("takserver-sso volume is optional so sso.enabled=false pods can schedule", () => {
  const p = certVolume("takserver-api");
  assert.equal(addRender(p, IMAGE), true);
  const sso = p.spec?.volumes?.find(v => v.name === "oidc-secret");
  assert.deepEqual(sso?.secret, { secretName: "takserver-sso", optional: true });
});
