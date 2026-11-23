// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only

import { a, Capability, metricsCollector, type PeprValidateRequest } from "pepr";
import type { KubernetesObject } from "kubernetes-fluent-client";
import { TakClient } from "../cluster.ts";
import { CLIENT_NAMESPACE, type TakClientDoc } from "./takclient.ts";
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
  type ConfigMapPolicyDoc,
  type PodPolicyDoc,
  type ServicePolicyDoc,
} from "./tak-policy.ts";

export const ALLOWED_METRIC = "tak_policy_allowed";
export const DENIED_METRIC = "tak_policy_denied";

metricsCollector.addCounter(ALLOWED_METRIC, "TAK validating policy approvals");
metricsCollector.addCounter(DENIED_METRIC, "TAK validating policy denials");

export const TakPolicy = new Capability({
  name: "takserver-policy",
  description: "Enforce TAK Zero Trust, plaintext, CA PEM, and TakClient admission policies",
  namespaces: [TAK_NAMESPACE, CLOUDTAK_NAMESPACE, CLIENT_NAMESPACE],
});

const { When } = TakPolicy;

export type ValidateRequest<T extends KubernetesObject> = Pick<
  PeprValidateRequest<T>,
  "Raw" | "Approve" | "Deny"
>;

export function respond<T extends KubernetesObject>(req: ValidateRequest<T>, reason: string | undefined) {
  if (reason) {
    metricsCollector.incCounter(DENIED_METRIC);
    return req.Deny(reason, 422);
  }
  metricsCollector.incCounter(ALLOWED_METRIC);
  return req.Approve();
}

export function onValidatePod(req: ValidateRequest<InstanceType<typeof a.Pod>>) {
  return respond(req, validatePodZeroTrust(req.Raw as unknown as PodPolicyDoc));
}

export function onValidateService(req: ValidateRequest<InstanceType<typeof a.Service>>) {
  return respond(req, validateServicePolicy(req.Raw as unknown as ServicePolicyDoc));
}

export function onValidateCoreConfig(req: ValidateRequest<InstanceType<typeof a.ConfigMap>>) {
  return respond(req, validateCoreConfig(req.Raw as unknown as ConfigMapPolicyDoc));
}

export function onValidateCertMigration(req: ValidateRequest<InstanceType<typeof a.ConfigMap>>) {
  return respond(req, validateCertMigration(req.Raw as unknown as ConfigMapPolicyDoc));
}

export function onValidateTakClient(req: ValidateRequest<TakClient>) {
  return respond(req, validateTakClientAdmission(req.Raw as unknown as TakClientDoc));
}

When(a.Pod).IsCreatedOrUpdated().InNamespace(TAK_NAMESPACE, CLOUDTAK_NAMESPACE).Validate(onValidatePod);
When(a.Service).IsCreatedOrUpdated().InNamespace(TAK_NAMESPACE).Validate(onValidateService);
When(a.ConfigMap).IsCreatedOrUpdated().InNamespace(TAK_NAMESPACE).WithName(CORE_CONFIG_NAME).Validate(onValidateCoreConfig);
When(a.ConfigMap).IsCreatedOrUpdated().InNamespace(TAK_NAMESPACE).WithName(CERT_MIGRATION_NAME).Validate(onValidateCertMigration);
When(TakClient).IsCreatedOrUpdated().InNamespace(CLIENT_NAMESPACE).Validate(onValidateTakClient);
