# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

{{- $enabled := printf "%v" .Values.backup.filesystem.enabled }}
{{- if eq $enabled "###ZARF_VAR_FILESYSTEM_BACKUP###" }}
{{- $enabled = .Variables.FILESYSTEM_BACKUP }}
{{- end }}
{{- $owner := .Values.backup.filesystem.owner }}
{{- if eq $owner "###ZARF_VAR_FILESYSTEM_BACKUP_OWNER###" }}
{{- $owner = .Variables.FILESYSTEM_BACKUP_OWNER }}
{{- end }}
backup:
  filesystem:
    enabled: {{ $enabled | quote }}
    owner: {{ $owner | quote }}
