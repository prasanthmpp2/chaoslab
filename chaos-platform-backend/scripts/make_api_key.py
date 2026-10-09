"""Generate an API key and the CHAOS_API_KEYS JSON entry. Usage: python scripts/make_api_key.py USER ROLE[,ROLE]"""
import hashlib
import json
import secrets
import sys

user, roles = sys.argv[1], sys.argv[2].split(",")
key = secrets.token_urlsafe(32)
entry = {hashlib.sha256(key.encode()).hexdigest(): {"user": user, "roles": roles}}
print("API key (store securely, shown once):", key)
print("Merge into CHAOS_API_KEYS:", json.dumps(entry))
