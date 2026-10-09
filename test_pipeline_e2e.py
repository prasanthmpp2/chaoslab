import io
import json
import time
import urllib.request
import zipfile

API = "http://127.0.0.1:8000/api/v1"
HEADERS = {
    "X-API-Key": "61ALqQzHWPLW6ic75iLRQJAs5xOztjk8xzKnr5JAOrU"
}

def make_project_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        html = (
            "<!DOCTYPE html><html><head><title>E2E App</title></head>"
            "<body><h1>Resilience Tested Microservice</h1>"
            "<p id='status'>UP</p></body></html>"
        )
        dockerfile = (
            "FROM nginx:1.27-alpine\n"
            "COPY index.html /usr/share/nginx/html/index.html\n"
            "EXPOSE 80\n"
            'CMD ["nginx", "-g", "daemon off;"]\n'
        )
        z.writestr("index.html", html)
        z.writestr("Dockerfile", dockerfile)
    return buf.getvalue()

def run_test():
    print("1. Preparing user project ZIP archive...")
    zip_bytes = make_project_zip()
    print(f"   Created project zip: {len(zip_bytes)} bytes")

    print("2. Uploading project to CI/CD pipeline...")
    url = (
        f"{API}/pipeline/upload"
        f"?project_name=user-microservice"
        f"&container_port=80"
        f"&health_path=/"
        f"&fault_engine=pumba"
        f"&fault_type=container-pause"
        f"&fault_duration=4"
        f"&auto_cleanup=true"
        f"&max_error_rate=0.1"
    )
    req = urllib.request.Request(
        url,
        data=zip_bytes,
        headers={**HEADERS, "Content-Type": "application/octet-stream"},
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        res = json.loads(resp.read())
        print(f"   Pipeline initiated: ID={res['pipeline_id']} Status={res['status']}")
        pipeline_id = res["pipeline_id"]

    print("3. Monitoring CI/CD pipeline stages and chaos test...")
    start_time = time.time()
    final_data = None
    for attempt in range(25):
        time.sleep(2)
        elapsed = int(time.time() - start_time)
        status_req = urllib.request.Request(f"{API}/pipeline/{pipeline_id}", headers=HEADERS)
        with urllib.request.urlopen(status_req) as resp:
            data = json.loads(resp.read())
            stages_summary = " -> ".join(f"{s['name']}: {s['status']}" for s in data["stages"] if s["status"] != "pending")
            print(f"   [{elapsed}s] Pipeline Status: {data['status']} | Active: {data['current_stage']}")
            print(f"       Stages: {stages_summary}")
            if data["status"] in ("PASSED", "FAILED"):
                final_data = data
                break

    assert final_data is not None, "Pipeline timed out"
    print("\n4. Pipeline Completed!")
    print(f"   Final Status: {final_data['status']}")
    print(f"   Verdict: {json.dumps(final_data['verdict'], indent=2)}")
    print("\n5. Build & Test Console Logs:")
    for log in final_data["logs"]:
        print(f"   [{log['level']}] {log['message']}")

    print("\n✅ E2E CI/CD Pipeline Verification Successful!")

if __name__ == "__main__":
    run_test()
