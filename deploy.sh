#!/bin/bash
set -e

# Read GEMINI vars
GEMINI_API_KEY=$(jq -r '.keys[0].key' ~/.config/gemini/credentials.json)
GEMINI_MODEL=$(jq -r '.model' ~/.config/gemini/credentials.json)

# Read ClickHouse vars
source ~/.config/flashframe/clickhouse.env

echo "Deploying to Cloud Run..."
gcloud run deploy flashframe \
  --source . \
  --project gen-lang-client-0466446073 \
  --region us-central1 \
  --memory 2Gi \
  --cpu 2 \
  --timeout 600 \
  --min-instances 0 \
  --allow-unauthenticated \
  --set-env-vars="GEMINI_API_KEY=${GEMINI_API_KEY},GEMINI_MODEL=${GEMINI_MODEL},CLICKHOUSE_HOST=${CLICKHOUSE_HOST},CLICKHOUSE_PORT=${CLICKHOUSE_PORT},CLICKHOUSE_USER=${CLICKHOUSE_USER},CLICKHOUSE_PASSWORD=${CLICKHOUSE_PASSWORD},CLICKHOUSE_DATABASE=${CLICKHOUSE_DATABASE},CLICKHOUSE_ALLOW_WRITE_ACCESS=${CLICKHOUSE_ALLOW_WRITE_ACCESS}"
