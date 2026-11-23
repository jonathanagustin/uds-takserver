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

export interface SecurityContextDoc {
  runAsUser?: number;
  runAsGroup?: number;
  fsGroup?: number;
  runAsNonRoot?: boolean;
  privileged?: boolean;
  allowPrivilegeEscalation?: boolean;
  procMount?: string;
  seLinuxOptions?: unknown;
  seccompProfile?: { type?: string };
  capabilities?: { add?: string[]; drop?: string[] };
}

export interface ContainerPolicyDoc {
  name?: string;
  securityContext?: SecurityContextDoc;
  ports?: { hostPort?: number }[];
}

export interface PodPolicyDoc {
  metadata?: {
    name?: string;
    labels?: Record<string, string>;
    annotations?: Record<string, string>;
  };
  spec?: {
    securityContext?: SecurityContextDoc;
    hostNetwork?: boolean;
    hostPID?: boolean;
    hostIPC?: boolean;
    volumes?: Record<string, unknown>[];
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
const ALLOWED_SECCOMP_TYPES = new Set(["RuntimeDefault", "Localhost"]);
const ALLOWED_VOLUME_TYPES = new Set([
  "configMap",
  "csi",
  "downwardAPI",
  "emptyDir",
  "ephemeral",
  "image",
  "persistentVolumeClaim",
  "projected",
  "secret",
]);
const BLOCKED_TRAFFIC_ANNOTATIONS = [
  "traffic.sidecar.istio.io/excludeInboundPorts",
  "traffic.sidecar.istio.io/excludeInterfaces",
  "traffic.sidecar.istio.io/excludeOutboundIPRanges",
  "traffic.sidecar.istio.io/excludeOutboundPorts",
  "traffic.sidecar.istio.io/includeInboundPorts",
  "traffic.sidecar.istio.io/includeOutboundIPRanges",
  "traffic.sidecar.istio.io/includeOutboundPorts",
  "sidecar.istio.io/interceptionMode",
  "traffic.sidecar.istio.io/kubevirtInterfaces",
  "istio.io/redirect-virtual-interfaces",
] as const;
const BLOCKED_SIDECAR_ANNOTATIONS = [
  "sidecar.istio.io/bootstrapOverride",
  "sidecar.istio.io/discoveryAddress",
  "sidecar.istio.io/proxyImage",
  "sidecar.istio.io/userVolume",
  "sidecar.istio.io/userVolumeMount",
] as const;

function validateSecurityContext(ctx: SecurityContextDoc | undefined, scope: string): string | undefined {
  if (!ctx) {
    return undefined;
  }
  if (ctx.runAsUser === 1337 || ctx.runAsGroup === 1337 || ctx.fsGroup === 1337) {
    return `${scope} must not run as reserved Istio UID/GID 1337`;
  }
  if (ctx.runAsUser === 0 || ctx.runAsNonRoot === false) {
    return `${scope} must not run as root`;
  }
  if (ctx.seccompProfile?.type !== undefined && !ALLOWED_SECCOMP_TYPES.has(ctx.seccompProfile.type)) {
    return `${scope} seccompProfile type ${ctx.seccompProfile.type} is forbidden`;
  }
  if (ctx.seLinuxOptions !== undefined) {
    return `${scope} seLinuxOptions are forbidden`;
  }
  return undefined;
}

export function validatePodZeroTrust(pod: PodPolicyDoc): string | undefined {
  const labels = pod.metadata?.labels ?? {};
  const annotations = pod.metadata?.annotations ?? {};
  if (labels["sidecar.istio.io/inject"] !== undefined || annotations["sidecar.istio.io/inject"] !== undefined) {
    return "Istio sidecar.istio.io/inject label or annotation is forbidden in ambient mesh namespaces";
  }
  if (annotations["ambient.istio.io/bypass-inbound-capture"] !== undefined) {
    return "ambient.istio.io/bypass-inbound-capture annotation is forbidden";
  }
  const blockedTraffic = BLOCKED_TRAFFIC_ANNOTATIONS.find(key => annotations[key] !== undefined);
  if (blockedTraffic) {
    return `Istio traffic annotation ${blockedTraffic} is forbidden`;
  }
  const blockedSidecar = BLOCKED_SIDECAR_ANNOTATIONS.find(key => annotations[key] !== undefined);
  if (blockedSidecar) {
    return `Istio sidecar annotation ${blockedSidecar} is forbidden`;
  }
  if (
    annotations["proxy.istio.io/config"] !== undefined &&
    !(pod.metadata?.name ?? "").startsWith("takserver-db-setup-")
  ) {
    return "Istio proxy.istio.io/config annotation is forbidden outside takserver-db-setup";
  }
  const spec = pod.spec ?? {};
  if (spec.hostNetwork || spec.hostPID || spec.hostIPC) {
    return "hostNetwork, hostPID, and hostIPC are forbidden";
  }
  for (const volume of spec.volumes ?? []) {
    const badType = Object.keys(volume).find(
      key => key !== "name" && volume[key] !== undefined && !ALLOWED_VOLUME_TYPES.has(key),
    );
    if (badType) {
      return `volume type ${badType} is forbidden`;
    }
  }
  const podSecurityProblem = validateSecurityContext(spec.securityContext, "pod");
  if (podSecurityProblem) {
    return podSecurityProblem;
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
    const ctx = container.securityContext;
    const ctxProblem = validateSecurityContext(ctx, `container ${container.name}`);
    if (ctxProblem) {
      return ctxProblem;
    }
    if (ctx?.privileged) {
      return `container ${container.name} must not set privileged: true`;
    }
    if (ctx?.allowPrivilegeEscalation) {
      return `container ${container.name} must not set allowPrivilegeEscalation: true`;
    }
    if (ctx?.procMount !== undefined && ctx.procMount !== "Default") {
      return `container ${container.name} procMount ${ctx.procMount} is forbidden`;
    }
    if ((ctx?.capabilities?.add ?? []).length > 0) {
      return `container ${container.name} must not add capabilities`;
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
