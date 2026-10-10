import urllib.request
import json
import time
import os

API = "http://127.0.0.1:8000/api/v1"
HEADERS = {}

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

def req_as_approver(method, url):
    key = os.environ.get("CHAOS_TEST_APPROVER_API_KEY")
    if not key:
        raise SystemExit("Set CHAOS_TEST_APPROVER_API_KEY to a different configured approver identity")
    req_obj = urllib.request.Request(url, method=method, headers={"X-API-Key": key})
    with urllib.request.urlopen(req_obj) as response:
        return json.loads(response.read().decode("utf-8"))


def main():
    api_key = os.environ.get("CHAOS_TEST_API_KEY")
    if not api_key:
        raise SystemExit("Set CHAOS_TEST_API_KEY to a configured author/operator identity")
    global HEADERS
    HEADERS = {"X-API-Key": api_key}
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
    req_as_approver("POST", f"{API}/experiments/{exp_id}/approve")

    print("Starting...")
    run = req("POST", f"{API}/experiments/{exp_id}/runs", data={"environment": "docker-test"})
    run_id = run.get("id") or run.get("run_id")
    print(f"Run ID: {run_id}")

    print("Polling...")
    for i in range(15):
        status_data = req("GET", f"{API}/runs/{run_id}")
        status = status_data['status']
        print(f"Status: {status}")
        if status in ['SUCCEEDED', 'FAILED', 'ABORTED', 'CLEANUP_FAILED']:
            break
        time.sleep(2)

    print("Cleanup faults...")
    faults = req("GET", f"{API}/runs/{run_id}/faults")
    print("Faults:", json.dumps(faults, indent=2))


if __name__ == "__main__":
    main()
