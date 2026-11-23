// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only

// Edits to the MutatingWebhookConfiguration that `pepr build` writes.
//
// Scope. The generated webhook matches every namespace except kube-system and
// pepr-system, and fails closed. It is narrowed to namespace takserver and to
// the objects the capabilities act on. The upstream chart labels the pods
// (`app: takserver-api` and so on) but puts no labels on the Deployments or
// on the Ignite ConfigMap, and the Ignite StatefulSet has no `app` label. An
// object selector on `app` therefore let only the pods through. A
// matchConditions rule matches the pods by label and the other objects by
// name, so admission shapes them before they are stored.
//
// Literals. Pepr turns the webhook timeout and the failure policy into chart
// values by replacing every occurrence of their text in the generated YAML.
// That also rewrites the same text inside the webhook path and the CA bundle,
// for example a "10" in the path hash. Those values render back only while
// the chart values equal the build-time ones. This puts the literal back
// everywhere except the timeoutSeconds and failurePolicy fields.

import { POLICY_CONFIGMAPS } from "../capabilities/tak-policy.ts";
import { NAMESPACE, TAK_APPS } from "../capabilities/oidc-render.ts";
import { IGNITE_CONFIGMAP, IGNITE_STATEFULSET, TAK_DEPLOYS } from "../capabilities/workload.ts";

export const TIMEOUT_VALUE = "{{ .Values.admission.webhookTimeout }}";
export const FAILURE_VALUE = "{{ .Values.admission.failurePolicy }}";
export const MATCH_CONDITION = "takserver-objects";
export const VALIDATION_MATCH_CONDITION = "takserver-policy-objects";

function celList(values: readonly string[]): string {
  return `[${values.map(v => `'${v}'`).join(", ")}]`;
}

// Pods are matched by the app label, because a new pod's name is generated.
// Every other kind in the webhook rules is matched by name.
export function matchExpression(): string {
  return [
    `request.kind.kind == 'Deployment' ? object.metadata.name in ${celList(TAK_DEPLOYS)} :`,
    `request.kind.kind == 'ConfigMap' ? object.metadata.name == '${IGNITE_CONFIGMAP}' :`,
    `request.kind.kind == 'StatefulSet' ? object.metadata.name == '${IGNITE_STATEFULSET}' :`,
    `has(object.metadata.labels) && 'app' in object.metadata.labels &&`,
    `object.metadata.labels['app'] in ${celList(TAK_APPS)}`,
  ].join(" ");
}

export function validationMatchExpression(): string {
  return `request.kind.kind == 'ConfigMap' ? object.metadata.name in ${celList(POLICY_CONFIGMAPS)} : true`;
}

// Replaces the generated namespaceSelector (and anything else up to `rules:`)
// in each webhook entry. Throws when an anchor is missing, so a Pepr upgrade
// that changes the template fails the build instead of shipping a wider
// webhook.
export function scopeWebhook(text: string): string {
  const start = text.indexOf("    namespaceSelector:\n");
  const end = text.indexOf("    rules:\n");
  if (start < 0 || end < start) {
    throw new Error("mutation-webhook.yaml no longer has the namespaceSelector anchor");
  }
  const scope = [
    "    namespaceSelector:",
    "      matchLabels:",
    `        kubernetes.io/metadata.name: ${NAMESPACE}`,
    "    matchConditions:",
    `      - name: ${MATCH_CONDITION}`,
    "        expression: >-",
    `          ${matchExpression()}`,
    "",
  ].join("\n");
  return text.slice(0, start) + scope + text.slice(end);
}

export function failurePolicy(onError: string | undefined): string {
  return onError === "reject" ? "Fail" : "Ignore";
}

export function pinLiterals(text: string, timeout: number, policy: string): string {
  const pinned = text.replaceAll(TIMEOUT_VALUE, String(timeout)).replaceAll(FAILURE_VALUE, policy);
  const timeoutField = /^( +timeoutSeconds: )\S+$/gm;
  const policyField = /^( +failurePolicy: )\S+$/gm;
  if (pinned.match(timeoutField) === null || pinned.match(policyField) === null) {
    throw new Error("webhook YAML has no timeoutSeconds or failurePolicy field");
  }
  return pinned.replace(timeoutField, `$1${TIMEOUT_VALUE}`).replace(policyField, `$1${FAILURE_VALUE}`);
}

function webhookEntry(
  header: string,
  webhookName: string,
  namespace: string,
  rules: string,
  tail: string,
  match?: { name: string; expression: string },
): string {
  const matchBlock = match
    ? [
        "    matchConditions:",
        `      - name: ${match.name}`,
        "        expression: >-",
        `          ${match.expression}`,
        "",
      ].join("\n")
    : "";
  return [
    header.replace("name: {{ .Values.uuid }}.pepr.dev", `name: ${webhookName}`),
    `    namespaceSelector:\n      matchLabels:\n        kubernetes.io/metadata.name: ${namespace}\n`,
    matchBlock,
    "    rules:\n",
    rules,
    tail.replace(/\n*$/, "\n"),
  ].join("");
}

// Cuts the TakClient rule out of the TAK webhook entry and adds a second entry
// that matches only TakClients in namespace takclients (ADR 22). The second
// entry copies the first one's path, CA bundle, timeout, and failure policy,
// but not its matchConditions, which match only TAK objects. Run it after
// scopeWebhook.
export function splitTakClientWebhook(text: string): string {
  const ruleStart = text.indexOf("      - apiGroups:\n          - tak.uds.dev\n");
  const ruleEnd = text.indexOf("    sideEffects:", ruleStart);
  const entry = text.indexOf("  - admissionReviewVersions:");
  if (ruleStart < 0 || ruleEnd < 0 || entry < 0) {
    throw new Error("mutation-webhook.yaml no longer has the TakClient rule anchor");
  }
  const rule = text.slice(ruleStart, ruleEnd);
  const pods = text.slice(0, ruleStart) + text.slice(ruleEnd);
  const first = pods.slice(entry);
  const selectorStart = first.indexOf("    namespaceSelector:\n");
  const tailStart = first.indexOf("    sideEffects:");
  const second = webhookEntry(
    first.slice(0, selectorStart),
    "takclients.{{ .Values.uuid }}.pepr.dev",
    "takclients",
    rule,
    first.slice(tailStart),
  );
  return `${pods.replace(/\n*$/, "\n")}${second}`;
}

// Splits Pepr's cluster-wide validation webhook into fail-closed namespace-scoped
// entries for takserver, cloudtak, and takclients so unrelated namespaces remain
// unaffected even when failurePolicy is Fail.
export function scopeValidationWebhook(text: string): string {
  const entry = text.indexOf("  - admissionReviewVersions:");
  const selectorStart = text.indexOf("    namespaceSelector:\n", Math.max(0, entry));
  const rulesStart = text.indexOf("    rules:\n", Math.max(0, selectorStart));
  const tailStart = text.indexOf("    sideEffects:", Math.max(0, rulesStart));
  if (entry < 0 || selectorStart < 0 || rulesStart < 0 || tailStart < 0) {
    throw new Error("validation-webhook.yaml no longer has the expected webhook anchors");
  }
  const rulesText = text.slice(rulesStart + "    rules:\n".length, tailStart);
  const blocks = rulesText.split(/(?=^      - apiGroups:\n)/m);
  const byResource = (resource: string): string => {
    const match = blocks.find(block => new RegExp(`^          - ${resource}$`, "m").test(block));
    if (!match) {
      throw new Error(`validation-webhook.yaml is missing the ${resource} rule`);
    }
    return match;
  };
  const podsRule = byResource("pods");
  const servicesRule = byResource("services");
  const configMapsRule = byResource("configmaps");
  const takClientsRule = byResource("takclients");
  const header = text.slice(entry, selectorStart);
  const tail = text.slice(tailStart);
  return [
    text.slice(0, entry),
    webhookEntry(
      header,
      "takserver-policy.{{ .Values.uuid }}.pepr.dev",
      "takserver",
      `${podsRule}${servicesRule}${configMapsRule}`,
      tail,
      { name: VALIDATION_MATCH_CONDITION, expression: validationMatchExpression() },
    ),
    webhookEntry(header, "cloudtak-policy.{{ .Values.uuid }}.pepr.dev", "cloudtak", podsRule, tail),
    webhookEntry(header, "takclients-policy.{{ .Values.uuid }}.pepr.dev", "takclients", takClientsRule, tail),
  ].join("");
}
