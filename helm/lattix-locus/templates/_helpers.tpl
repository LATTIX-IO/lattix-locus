{{- /* Existing pre-rename releases set nameOverride/fullnameOverride: lattix-frontier
     so Deployment selectors and StatefulSet PVC names stay unchanged on upgrade. */ -}}
{{- define "lattix-locus.name" -}}
{{- default "lattix-locus" .Values.nameOverride -}}
{{- end -}}

{{- define "lattix-locus.fullname" -}}
{{- default (include "lattix-locus.name" .) .Values.fullnameOverride -}}
{{- end -}}

{{- define "lattix-locus.labels" -}}
app.kubernetes.io/name: {{ include "lattix-locus.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ include "lattix-locus.name" . }}-{{ .Chart.Version }}
{{- end -}}

{{- define "lattix-locus.imageRef" -}}
{{- $image := . -}}
{{- if kindIs "string" $image -}}
{{- $image -}}
{{- else -}}
{{- $repository := default "" $image.repository -}}
{{- $tag := default "" $image.tag -}}
{{- $digest := default "" $image.digest -}}
{{- if and $repository $digest -}}
{{- printf "%s@%s" $repository $digest -}}
{{- else if and $repository $tag -}}
{{- printf "%s:%s" $repository $tag -}}
{{- else -}}
{{- $repository -}}
{{- end -}}
{{- end -}}
{{- end -}}
