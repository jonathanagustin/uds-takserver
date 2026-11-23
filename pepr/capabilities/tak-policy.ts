// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only

// Pure admission validators for TAK Zero Trust, plaintext prohibition, CA PEM
// integrity, and TakClient resource correctness.

import { X509Certificate } from "node:crypto";
import { TAK_NAMESPACE, validate, type TakClientDoc } from "./takclient.ts";

export { TAK_NAMESPACE };
export const CLOUDTAK_NAMESPACE = "cloudtak";
export const CORE_CONFIG_NAME = "core-config";
export const CERT_MIGRATION_NAME = "cert-migration";
export const POLICY_CONFIGMAPS = [CORE_CONFIG_NAME, CERT_MIGRATION_NAME] as const;

export interface ContainerPolicyDoc {
  name?: string;
  securityContext?: {
    privileged?: boolean;
    allowPrivilegeEscalation?: boolean;
  };
  ports?: { hostPort?: number }[];
}

export interface PodPolicyDoc {
  metadata?: {
    labels?: Record<string, string>;
    annotations?: Record<string, string>;
  };
  spec?: {
    hostNetwork?: boolean;
    hostPID?: boolean;
    hostIPC?: boolean;
    volumes?: { hostPath?: unknown }[];
    containers?: ContainerPolicyDoc[];
    initContainers?: ContainerPolicyDoc[];
    ephemeralContainers?: ContainerPolicyDoc[];
  };
}

export interface ServicePolicyDoc {
  metadata?: { name?: string };
  spec?: {
    type?: string;
    ports?: { name?: string; port?: number; targetPort?: number | string }[];
  };
}

export interface ConfigMapPolicyDoc {
  data?: Record<string, string>;
  binaryData?: Record<string, string>;
}

const FORBIDDEN_SIDECAR_CONTAINERS = new Set(["istio-proxy", "istio-validation"]);
const FORBIDDEN_SERVICE_TYPES = new Set(["NodePort", "ExternalName"]);
const TLS_FALSE_RE = /tls\s*=\s*["']false["']/i;

export function validatePodZeroTrust(pod: PodPolicyDoc): string | undefined {
  const labels = pod.metadata?.labels ?? {};
  const annotations = pod.metadata?.annotations ?? {};
  if (labels["sidecar.istio.io/inject"] === "true" || annotations["sidecar.istio.io/inject"] === "true") {
    return "Istio sidecar injection is forbidden in ambient mesh namespaces";
  }
  if (annotations["ambient.istio.io/bypass-inbound-capture"] !== undefined) {
    return "ambient.istio.io/bypass-inbound-capture annotation is forbidden";
  }
  const spec = pod.spec ?? {};
  if (spec.hostNetwork || spec.hostPID || spec.hostIPC) {
    return "hostNetwork, hostPID, and hostIPC are forbidden";
  }
  if ((spec.volumes ?? []).some(volume => volume.hostPath !== undefined)) {
    return "hostPath volumes are forbidden";
  }
  const allContainers = [
    ...(spec.containers ?? []),
    ...(spec.initContainers ?? []),
    ...(spec.ephemeralContainers ?? []),
  ];
  for (const container of allContainers) {
    if (FORBIDDEN_SIDECAR_CONTAINERS.has(container.name ?? "")) {
      return `Istio sidecar container ${container.name} is forbidden`;
    }
    if (container.securityContext?.privileged) {
      return `container ${container.name} must not set privileged: true`;
    }
    if (container.securityContext?.allowPrivilegeEscalation) {
      return `container ${container.name} must not set allowPrivilegeEscalation: true`;
    }
    if ((container.ports ?? []).some(port => typeof port.hostPort === "number" && port.hostPort > 0)) {
      return `container ${container.name} must not expose a hostPort`;
    }
  }
  return undefined;
}

export function validateServicePolicy(service: ServicePolicyDoc): string | undefined {
  const svcType = service.spec?.type ?? "";
  if (FORBIDDEN_SERVICE_TYPES.has(svcType)) {
    return `Service type ${svcType} is forbidden in ${TAK_NAMESPACE}`;
  }
  const name = service.metadata?.name ?? "";
  for (const port of service.spec?.ports ?? []) {
    const isUpstreamIgniteRest = name === "takserver-ignite" && port.name === "rest";
    if (!isUpstreamIgniteRest && (port.port === 8080 || port.targetPort === 8080 || port.targetPort === "8080")) {
      return "Services must not publish plaintext port 8080";
    }
  }
  return undefined;
}

export function validateCoreConfig(configMap: ConfigMapPolicyDoc): string | undefined {
  const xml = configMap.data?.["CoreConfig.xml"] ?? "";
  if (TLS_FALSE_RE.test(xml)) {
    return 'CoreConfig.xml must not contain plaintext tls="false" connectors';
  }
  return undefined;
}

export function validateCertMigration(configMap: ConfigMapPolicyDoc): string | undefined {
  const encoded = configMap.binaryData?.["ca.pem"];
  const inline = configMap.data?.["ca.pem"];
  const raw = encoded !== undefined ? Buffer.from(encoded, "base64").toString("utf8") : inline;
  if (raw === undefined) {
    return undefined;
  }
  try {
    new X509Certificate(raw);
    return undefined;
  } catch {
    return "cert-migration ca.pem must be a valid X.509 PEM certificate";
  }
}

export function validateTakClientAdmission(client: TakClientDoc): string | undefined {
  return validate(client.spec);
}
