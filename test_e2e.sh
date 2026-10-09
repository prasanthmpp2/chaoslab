#!/bin/bash
set -e

# API Base
API="http://api:8000/api/v1"

echo "1. Checking engines..."
curl -s $API/engines | jq

echo "2. Creating experiment..."
EXP_ID=$(curl -s -X POST $API/experiments -H "Content-Type: application/json" -d '{
  "title": "E2E Test Experiment",
  "description": "Test E2E",
  "engine": "toxiproxy",
  "environment": "dev",
  "definition": {
    "engine": "toxiproxy",
    "target": "payment-http",
    "fault_type": "latency",
    "parameters": {
      "latency": 500,
      "jitter": 50
    }
  }
}' | jq -r '.id')

echo "Experiment ID: $EXP_ID"

if [ "$EXP_ID" == "null" ] || [ -z "$EXP_ID" ]; then
    echo "Failed to create experiment."
    exit 1
fi

echo "3. Starting run..."
RUN_ID=$(curl -s -X POST $API/experiments/$EXP_ID/runs -H "Content-Type: application/json" -d '{}' | jq -r '.id')

echo "Run ID: $RUN_ID"

echo "4. Polling run status..."
for i in {1..10}; do
    STATUS=$(curl -s $API/runs/$RUN_ID | jq -r '.status')
    echo "Status: $STATUS"
    if [ "$STATUS" == "PASSED" ] || [ "$STATUS" == "FAILED" ] || [ "$STATUS" == "ERROR" ]; then
        break
    fi
    sleep 2
done

echo "5. Cleanup faults..."
curl -s $API/runs/$RUN_ID/faults | jq
