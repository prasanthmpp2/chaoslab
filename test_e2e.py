import urllib.request
import json
import time

API = "http://127.0.0.1:8000/api/v1"
HEADERS = {
    "X-API-Key": "61ALqQzHWPLW6ic75iLRQJAs5xOztjk8xzKnr5JAOrU"
}

def req(method, url, data=None):
    req_obj = urllib.request.Request(url, method=method, headers=HEADERS)
    if data:
        req_obj.add_header('Content-Type', 'application/json')
        req_obj.data = json.dumps(data).encode('utf-8')
    try:
        with urllib.request.urlopen(req_obj) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        print(f"HTTPError: {e.code}")
        print(e.read().decode('utf-8'))
        raise e

payload = {
    "apiVersion": "chaos.example.io/v1",
    "kind": "Experiment",
    "metadata": {
        "name": "e2e-test-" + str(int(time.time()))
    },
    "spec": {
        "target": {
            "service": "payment-service", "environment": "docker-test"
        },
        "fault": {
            "engine": "toxiproxy",
            "type": "network-latency",
            "durationSeconds": 30,
            "parameters": {
                "latencyMs": 500,
                "proxy": "payment-http"
            }
        },
        "safety": {
            "environmentAllowlist": ["docker-test"],
            "maxDurationSeconds": 60
        }
    }
}
print("Creating...")
exp = req("POST", f"{API}/experiments", data=payload)
exp_id = exp['id']
print(f"Experiment ID: {exp_id}")

print("Approving...")
req("POST", f"{API}/experiments/{exp_id}/approve")

print("Starting...")
run = req("POST", f"{API}/experiments/{exp_id}/runs", data={"environment": "docker-test"})
run_id = run.get("id") or run.get("run_id")
print(f"Run ID: {run_id}")

print("Polling...")
for i in range(15):
    status_data = req("GET", f"{API}/runs/{run_id}")
    status = status_data['status']
    print(f"Status: {status}")
    if status in ['PASSED', 'FAILED', 'ERROR']:
        break
    time.sleep(2)

print("Cleanup faults...")
faults = req("GET", f"{API}/runs/{run_id}/faults")
print("Faults:", json.dumps(faults, indent=2))
