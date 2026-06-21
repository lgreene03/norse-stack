{{/*
Common helpers for the norse-stack umbrella chart.
*/}}

{{/* Chart name, optionally overridden by .Values.nameOverride. */}}
{{- define "norse-stack.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Fully-qualified release name, e.g. <release>-norse-stack. */}}
{{- define "norse-stack.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* Common labels applied to every object. */}}
{{- define "norse-stack.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: {{ include "norse-stack.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: norse-stack
{{- end -}}

{{/*
Per-component selector labels. Call with a dict:
  (dict "ctx" $ "component" "huginn")
*/}}
{{- define "norse-stack.selectorLabels" -}}
app.kubernetes.io/name: {{ include "norse-stack.name" .ctx }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
Resolve a component image reference from global + per-service values.
Call with: (dict "ctx" $ "svc" .Values.huginn)
*/}}
{{- define "norse-stack.image" -}}
{{- $registry := .ctx.Values.global.imageRegistry -}}
{{- $repo := .svc.image.repository -}}
{{- $tag := default .ctx.Values.global.imageTag .svc.image.tag -}}
{{- if $registry -}}
{{- printf "%s/%s:%s" $registry $repo $tag -}}
{{- else -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end -}}
