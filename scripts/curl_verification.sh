#!/bin/bash
# curl_verification.sh
# Manual curl tests for Connect Laundry API format verification

BASE_URL="http://localhost:8000/api/v1"
echo "--- Testing Health Check (Success Format) ---"
curl -s -X GET "$BASE_URL/../health/" | jq

echo "\n--- Testing 404 Not Found (DRF Exception) ---"
curl -s -X GET "$BASE_URL/non-existent-endpoint/" | jq

echo "\n--- Testing Unauthorized Access (401) ---"
curl -s -X GET "$BASE_URL/laundries/dashboard/my-laundry/" | jq

echo "\n--- Testing Laundry List (Success) ---"
curl -s -X GET "$BASE_URL/laundries/laundries/" | jq

echo "\nVerification complete. Check that the root uses \"success\": true or false."
