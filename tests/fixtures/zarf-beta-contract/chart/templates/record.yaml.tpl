# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
apiVersion: v1
kind: ConfigMap
metadata:
  name: values-contract
data:
{{- range $name, $value := .Values.record }}
  {{ $name }}: {{ $value | quote }}
{{- end }}
