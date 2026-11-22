{{- $enabled := printf "%v" .Values.backup.filesystem.enabled }}
{{- if eq $enabled "###ZARF_VAR_FILESYSTEM_BACKUP###" }}
{{- $enabled = .Variables.FILESYSTEM_BACKUP }}
{{- end }}
{{- if eq $enabled "true" }}
# Bitnami PostgreSQL 12.1.6 names the data volume "data".
# Velero backs that volume up only when the node-agent is running.
postgresql:
  primary:
    podAnnotations:
      backup.velero.io/backup-volumes: data
{{- else }}
{}
{{- end }}
