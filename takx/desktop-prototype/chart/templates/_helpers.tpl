{{/*
Copyright 2026 uds-takserver authors
SPDX-License-Identifier: GPL-3.0-only
*/}}

{{- define "takx-desktop.selectorLabels" -}}
app.kubernetes.io/name: takx-desktop
{{- end }}

{{- define "takx-desktop.labels" -}}
{{ include "takx-desktop.selectorLabels" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/part-of: takx-desktop
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end }}

{{- define "takx-desktop.url" -}}
https://{{ .Values.host }}.{{ .Values.domain }}
{{- end }}

{{/*
Address of the passthrough gateway for the hosts entry of tak.certName:
tak.gateway.ip when set, otherwise the ClusterIP of the gateway Service when
a cluster answers the lookup, otherwise empty.
*/}}
{{- define "takx-desktop.gatewayIP" -}}
{{- $ip := .Values.tak.gateway.ip -}}
{{- if not $ip -}}
{{- $svc := lookup "v1" "Service" .Values.tak.gateway.namespace .Values.tak.gateway.service -}}
{{- if $svc -}}
{{- $ip = dig "spec" "clusterIP" "" $svc -}}
{{- if eq $ip "None" }}{{ $ip = "" }}{{ end -}}
{{- end -}}
{{- end -}}
{{- $ip -}}
{{- end }}

{{/* Security context shared by all three containers. */}}
{{- define "takx-desktop.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end }}
