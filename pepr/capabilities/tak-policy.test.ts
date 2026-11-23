// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only

import assert from "node:assert/strict";
import { generateKeyPairSync } from "node:crypto";
import { test } from "node:test";
import forge from "node-forge";
import { a, metricsCollector } from "pepr";
import type { TakClient } from "../cluster.ts";
import {
  onValidateCertMigration,
  onValidateCoreConfig,
  onValidatePod,
  onValidateService,
  onValidateTakClient,
  TakPolicy,
  type ValidateRequest,
} from "./tak-policy-capability.ts";
import {
  CERT_MIGRATION_NAME,
  CLOUDTAK_NAMESPACE,
  CORE_CONFIG_NAME,
  TAK_NAMESPACE,
  validateCertMigration,
  validateCoreConfig,
  validatePodZeroTrust,
  validateServicePolicy,
  validateTakClientAdmission,
} from "./tak-policy.ts";

function sampleCaPem(): string {
  const keypair = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const key = forge.pki.privateKeyFromPem(keypair.privateKey.export({ type: "pkcs1", format: "pem" }).toString());
  const cert = forge.pki.createCertificate();
  cert.publicKey = forge.pki.publicKeyFromPem(keypair.publicKey.export({ type: "spki", format: "pem" }).toString());
  cert.serialNumber = "01";
  cert.setSubject([{ shortName: "CN", value: "tak-policy-test-ca" }]);
  cert.setIssuer(cert.subject.attributes);
  cert.setExtensions([{ name: "basicConstraints", cA: true }]);
  cert.sign(key, forge.md.sha256.create());
  return forge.pki.certificateToPem(cert);
}

const VALID_CA_PEM = sampleCaPem();

function makeRequest<T extends { apiVersion?: string; kind?: string; metadata?: object }>(
  raw: unknown,
): ValidateRequest<T> {
  return {
    Raw: raw as T,
    Approve: warnings => ({ allowed: true, warnings }),
    Deny: (statusMessage, statusCode, warnings) => ({ allowed: false, statusMessage, statusCode, warnings }),
  };
}

test("TakPolicy registers all five fail-closed admission bindings across expected namespaces", () => {
  assert.equal(TakPolicy.name, "takserver-policy");
  assert.deepEqual(TakPolicy.namespaces, [TAK_NAMESPACE, CLOUDTAK_NAMESPACE, "takclients"]);
  const bindings = (TakPolicy as unknown as {
    bindings: {
      event: string;
      kind: { kind: string; group: string };
      filters: { name: string; namespaces: string[] };
      isValidate?: boolean;
    }[];
  }).bindings;
  assert.equal(bindings.length, 5);
  assert.deepEqual(
    bindings.map(b => `${b.kind.kind}:${b.event}:${b.filters.namespaces.join(",")}:${b.filters.name}`),
    [
      "Pod:CREATEORUPDATE:takserver,cloudtak:",
      "Service:CREATEORUPDATE:takserver:",
      `ConfigMap:CREATEORUPDATE:takserver:${CORE_CONFIG_NAME}`,
      `ConfigMap:CREATEORUPDATE:takserver:${CERT_MIGRATION_NAME}`,
      "TakClient:CREATEORUPDATE:takclients:",
    ],
  );
  assert.equal(bindings.every(b => b.isValidate === true), true);
});

test("validatePodZeroTrust allows compliant pods across container lists and empty fields", () => {
  assert.equal(validatePodZeroTrust({}), undefined);
  assert.equal(
    validatePodZeroTrust({
      metadata: {
        name: "takserver-db-setup-abc",
        labels: { app: "takserver-db-setup" },
        annotations: { "proxy.istio.io/config": "{}" },
      },
      spec: {
        securityContext: {
          runAsUser: 1000,
          runAsGroup: 1000,
          fsGroup: 1000,
          runAsNonRoot: true,
          seccompProfile: { type: "RuntimeDefault" },
        },
        volumes: [{ name: "undefined-hostpath", hostPath: undefined }, { name: "data", emptyDir: {} }],
        initContainers: [
          {
            name: "render-oidc",
            securityContext: {
              privileged: false,
              allowPrivilegeEscalation: false,
              procMount: "Default",
              seccompProfile: {},
              capabilities: { drop: ["ALL"], add: [] },
            },
          },
        ],
        containers: [{ ports: [{}, { hostPort: 0 }] }, { securityContext: { seccompProfile: { type: "Localhost" } } }],
        ephemeralContainers: [{ name: "debug" }],
      },
    }),
    undefined,
  );
});

test("validatePodZeroTrust rejects sidecar overrides, host namespaces, disallowed volumes, securityContext, and hostPorts", () => {
  assert.match(
    validatePodZeroTrust({ metadata: { labels: { "sidecar.istio.io/inject": "false" } } }) ?? "",
    /sidecar\.istio\.io\/inject/,
  );
  assert.match(
    validatePodZeroTrust({ metadata: { annotations: { "sidecar.istio.io/inject": "true" } } }) ?? "",
    /sidecar\.istio\.io\/inject/,
  );
  assert.match(
    validatePodZeroTrust({ metadata: { annotations: { "ambient.istio.io/bypass-inbound-capture": "true" } } }) ?? "",
    /bypass-inbound-capture/,
  );
  assert.match(
    validatePodZeroTrust({ metadata: { annotations: { "traffic.sidecar.istio.io/excludeOutboundPorts": "8080" } } }) ?? "",
    /traffic\.sidecar\.istio\.io\/excludeOutboundPorts/,
  );
  assert.match(
    validatePodZeroTrust({ metadata: { annotations: { "sidecar.istio.io/proxyImage": "evil" } } }) ?? "",
    /sidecar\.istio\.io\/proxyImage/,
  );
  assert.match(
    validatePodZeroTrust({ metadata: { annotations: { "proxy.istio.io/config": "{}" } } }) ?? "",
    /proxy\.istio\.io\/config/,
  );
  assert.match(
    validatePodZeroTrust({ metadata: { name: "takserver-api-0", annotations: { "proxy.istio.io/config": "{}" } } }) ?? "",
    /proxy\.istio\.io\/config/,
  );
  for (const field of ["hostNetwork", "hostPID", "hostIPC"] as const) {
    assert.match(validatePodZeroTrust({ spec: { [field]: true } }) ?? "", /hostNetwork, hostPID, and hostIPC/);
  }
  assert.match(validatePodZeroTrust({ spec: { volumes: [{ hostPath: { path: "/tmp" } }] } }) ?? "", /volume type hostPath/);
  assert.match(validatePodZeroTrust({ spec: { volumes: [{ nfs: { server: "1.2.3.4" } }] } }) ?? "", /volume type nfs/);
  assert.match(validatePodZeroTrust({ spec: { securityContext: { runAsUser: 1337 } } }) ?? "", /1337/);
  assert.match(validatePodZeroTrust({ spec: { securityContext: { runAsGroup: 1337 } } }) ?? "", /1337/);
  assert.match(validatePodZeroTrust({ spec: { securityContext: { fsGroup: 1337 } } }) ?? "", /1337/);
  assert.match(validatePodZeroTrust({ spec: { securityContext: { runAsUser: 0 } } }) ?? "", /run as root/);
  assert.match(validatePodZeroTrust({ spec: { securityContext: { runAsNonRoot: false } } }) ?? "", /run as root/);
  assert.match(
    validatePodZeroTrust({ spec: { securityContext: { seccompProfile: { type: "Unconfined" } } } }) ?? "",
    /seccompProfile type Unconfined/,
  );
  assert.match(
    validatePodZeroTrust({ spec: { securityContext: { seLinuxOptions: { level: "s0:c123" } } } }) ?? "",
    /seLinuxOptions/,
  );
  assert.match(
    validatePodZeroTrust({ spec: { containers: [{ name: "istio-proxy" }] } }) ?? "",
    /istio-proxy/,
  );
  assert.match(
    validatePodZeroTrust({ spec: { initContainers: [{ name: "istio-validation" }] } }) ?? "",
    /istio-validation/,
  );
  assert.match(
    validatePodZeroTrust({ spec: { containers: [{ name: "app", securityContext: { runAsUser: 0 } }] } }) ?? "",
    /container app must not run as root/,
  );
  assert.match(
    validatePodZeroTrust({ spec: { containers: [{ name: "app", securityContext: { privileged: true } }] } }) ?? "",
    /privileged: true/,
  );
  assert.match(
    validatePodZeroTrust({
      spec: { ephemeralContainers: [{ name: "dbg", securityContext: { allowPrivilegeEscalation: true } }] },
    }) ?? "",
    /allowPrivilegeEscalation: true/,
  );
  assert.match(
    validatePodZeroTrust({ spec: { containers: [{ name: "app", securityContext: { procMount: "Unmasked" } }] } }) ?? "",
    /procMount Unmasked/,
  );
  assert.match(
    validatePodZeroTrust({
      spec: { containers: [{ name: "app", securityContext: { capabilities: { add: ["NET_ADMIN"] } } }] },
    }) ?? "",
    /must not add capabilities/,
  );
  assert.match(
    validatePodZeroTrust({ spec: { containers: [{ name: "api", ports: [{ hostPort: 8443 }] }] } }) ?? "",
    /hostPort/,
  );
});

test("validateServicePolicy enforces Service types and plaintext 8080 prohibition with Ignite REST exception", () => {
  assert.equal(validateServicePolicy({}), undefined);
  assert.equal(
    validateServicePolicy({ metadata: { name: "takserver-api-service" }, spec: { type: "ClusterIP", ports: [{ port: 8443, targetPort: 8443 }] } }),
    undefined,
  );
  assert.equal(
    validateServicePolicy({
      metadata: { name: "takserver-ignite" },
      spec: { type: "ClusterIP", ports: [{ name: "rest", port: 8080, targetPort: 8080 }, { name: "sql", port: 10800 }] },
    }),
    undefined,
  );
  assert.match(validateServicePolicy({ spec: { type: "NodePort" } }) ?? "", /NodePort is forbidden/);
  assert.match(validateServicePolicy({ spec: { type: "ExternalName" } }) ?? "", /ExternalName is forbidden/);
  assert.match(
    validateServicePolicy({ metadata: { name: "takserver-api-service" }, spec: { ports: [{ port: 8080 }] } }) ?? "",
    /port 8080/,
  );
  assert.match(
    validateServicePolicy({ metadata: { name: "takserver-api-service" }, spec: { ports: [{ port: 443, targetPort: 8080 }] } }) ?? "",
    /port 8080/,
  );
  assert.match(
    validateServicePolicy({ metadata: { name: "takserver-api-service" }, spec: { ports: [{ port: 443, targetPort: "8080" }] } }) ?? "",
    /port 8080/,
  );
  assert.match(
    validateServicePolicy({ metadata: { name: "takserver-ignite" }, spec: { ports: [{ name: "custom", port: 8080 }] } }) ?? "",
    /port 8080/,
  );
});

test("validateCoreConfig rejects tls='false' connectors", () => {
  assert.equal(validateCoreConfig({}), undefined);
  assert.equal(validateCoreConfig({ data: { "CoreConfig.xml": '<network><connector port="8447" tls="true"/></network>' } }), undefined);
  assert.match(
    validateCoreConfig({ data: { "CoreConfig.xml": '<connector port="8080" tls="false"/>' } }) ?? "",
    /tls="false"/,
  );
  assert.match(
    validateCoreConfig({ data: { "CoreConfig.xml": "<connector tls = 'false'/>" } }) ?? "",
    /tls="false"/,
  );
});

test("validateCertMigration validates X.509 PEM certificates in binaryData or data", () => {
  assert.equal(validateCertMigration({}), undefined);
  assert.equal(
    validateCertMigration({ binaryData: { "ca.pem": Buffer.from(VALID_CA_PEM, "utf8").toString("base64") } }),
    undefined,
  );
  assert.equal(validateCertMigration({ data: { "ca.pem": VALID_CA_PEM } }), undefined);
  assert.match(
    validateCertMigration({ binaryData: { "ca.pem": Buffer.from("not-a-cert", "utf8").toString("base64") } }) ?? "",
    /X\.509 PEM/,
  );
  assert.match(validateCertMigration({ data: { "ca.pem": "" } }) ?? "", /X\.509 PEM/);
});

test("validateTakClientAdmission delegates to TakClient schema validator", () => {
  assert.equal(validateTakClientAdmission({ spec: { callsign: "alpha.1", groups: ["Operators"] } }), undefined);
  assert.match(validateTakClientAdmission({ spec: { callsign: "ab", groups: ["Operators"] } }) ?? "", /callsign/);
});

test("capability Validate callbacks return Approve() or Deny(..., 422) and update metrics", async () => {
  type Pod = InstanceType<typeof a.Pod>;
  type Service = InstanceType<typeof a.Service>;
  type ConfigMap = InstanceType<typeof a.ConfigMap>;

  assert.equal(onValidatePod(makeRequest<Pod>({ spec: { containers: [{ name: "api" }] } })).allowed, true);
  const badPod = onValidatePod(makeRequest<Pod>({ spec: { hostNetwork: true } }));
  assert.equal(badPod.allowed, false);
  assert.equal(badPod.statusCode, 422);

  assert.equal(onValidateService(makeRequest<Service>({ spec: { type: "ClusterIP" } })).allowed, true);
  const badSvc = onValidateService(makeRequest<Service>({ spec: { type: "NodePort" } }));
  assert.equal(badSvc.allowed, false);
  assert.equal(badSvc.statusCode, 422);

  assert.equal(
    onValidateCoreConfig(makeRequest<ConfigMap>({ data: { "CoreConfig.xml": "<configuration/>" } })).allowed,
    true,
  );
  assert.equal(
    onValidateCoreConfig(makeRequest<ConfigMap>({ data: { "CoreConfig.xml": '<connector tls="false"/>' } })).allowed,
    false,
  );

  assert.equal(onValidateCertMigration(makeRequest<ConfigMap>({ data: { "ca.pem": VALID_CA_PEM } })).allowed, true);
  assert.equal(onValidateCertMigration(makeRequest<ConfigMap>({ data: { "ca.pem": "broken" } })).allowed, false);

  assert.equal(
    onValidateTakClient(makeRequest<TakClient>({ spec: { callsign: "bravo-2", groups: ["Operators"] } })).allowed,
    true,
  );
  assert.equal(
    onValidateTakClient(makeRequest<TakClient>({ spec: { callsign: "bad name", groups: ["Operators"] } })).allowed,
    false,
  );

  const metrics = await metricsCollector.getMetrics();
  assert.match(metrics, /^pepr_tak_policy_allowed 5$/m);
  assert.match(metrics, /^pepr_tak_policy_denied 5$/m);
});
