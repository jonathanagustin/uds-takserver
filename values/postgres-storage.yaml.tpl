{{- $class := .Values.storage.postgresClass }}
{{- if eq $class "###ZARF_VAR_POSTGRES_STORAGE_CLASS###" }}
{{- $class = .Variables.POSTGRES_STORAGE_CLASS }}
{{- end }}
{{- if $class }}
# Bitnami PostgreSQL 12.1.6, the TAK chart's subchart. Set only when
# POSTGRES_STORAGE_CLASS or storage.postgresClass is not empty, so the default
# render is unchanged.
postgresql:
  primary:
    persistence:
      storageClass: {{ $class | quote }}
{{- else }}
{}
{{- end }}
