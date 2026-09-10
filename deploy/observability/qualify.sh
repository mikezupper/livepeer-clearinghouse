#!/bin/sh
set -eu

curl --fail --silent --show-error --max-time 5 http://api:8000/health/live >/dev/null

metric=false
trace=false
log=false
for _ in $(seq 1 30); do
  names=$(curl --fail --silent --show-error --max-time 5 \
    http://127.0.0.1:9090/api/v1/label/__name__/values || true)
  case "$names" in *clearinghouse_http_requests*) metric=true ;; esac

  traces=$(curl --fail --silent --show-error --max-time 5 --get \
    --data-urlencode 'limit=20' http://127.0.0.1:3200/api/search || true)
  case "$traces" in *'"rootServiceName":"clearinghouse-api"'*) trace=true ;; esac

  logs=$(curl --fail --silent --show-error --max-time 5 --get \
    --data-urlencode 'query={service_name="clearinghouse-api"}' \
    --data-urlencode 'limit=1' http://127.0.0.1:3100/loki/api/v1/query_range || true)
  case "$logs" in *'"result":[{'*) log=true ;; esac

  if [ "$metric" = true ] && [ "$trace" = true ] && [ "$log" = true ]; then
    printf '%s\n' '{"status":"ready","app_metric":"queryable","app_trace":"queryable","app_log":"queryable"}'
    exit 0
  fi
  sleep 1
done

printf '{"status":"degraded","app_metric":"%s","app_trace":"%s","app_log":"%s"}\n' \
  "$metric" "$trace" "$log" >&2
exit 2
