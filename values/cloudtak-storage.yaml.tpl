{{- $class := .Values.storage.cloudtakClass }}
{{- if eq $class (printf "###ZARF_%s###" "VAR_CLOUDTAK_STORAGE_CLASS") }}
{{- $class = .Variables.CLOUDTAK_STORAGE_CLASS }}
{{- end }}
{{- if $class }}
# CloudTAK PostGIS data volume. Set only when CLOUDTAK_STORAGE_CLASS or
# storage.cloudtakClass is not empty, so the default render is unchanged.
postgis:
  storageClass: {{ $class | quote }}
{{- else }}
{}
{{- end }}
