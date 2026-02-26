import subprocess
import time
import sys
import httpx

print("Starting server...")
proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "src.api.server:app",
     "--host", "0.0.0.0", "--port", "9000"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)

time.sleep(10)

try:
    with httpx.Client(trust_env=False, timeout=3) as client:
        client.get("http://127.0.0.1:9000/health")
    print("Сервер отвечает на /health")
except:
    print("Сервер НЕ отвечает!")

try:
    print("Sending scan request to moysklad.ru...")
    with httpx.Client(trust_env=False, timeout=120) as client:
        r = client.post(
            "http://127.0.0.1:9000/api/v1/scan",
            headers={"Authorization": "Bearer dev"},
            json={"url": "https://moysklad.ru", "max_pages": 3},
        )
    print("Status:", r.status_code)
    if r.status_code == 200:
        data = r.json()
        print("")
        print("=== РЕЗУЛЬТАТ СКАНА ===")
        print("  scan_id:                ", data["scan_id"])
        print("  status:                 ", data["status"])
        print("  pages_scanned:          ", data["pages_scanned"])
        print("  forms_found:            ", data["forms_found"])
        print("  external_scripts_found: ", data["external_scripts_found"])
        print("  privacy_policy_found:   ", data["privacy_policy_found"])
        print("  cookie_banner_found:    ", data["cookie_banner_found"])
    else:
        print("Error:", r.text[:500])
        print("Headers:", dict(r.headers))
        print("Body:", r.content)
except Exception as e:
    print("Request failed:", e)
finally:
    print("")
    print("Stopping server...")
    proc.terminate()
    proc.wait(timeout=5)
    print("SERVER LOGS:", proc.stderr.read().decode("cp1251", errors="ignore")[:1000])
    print("Done.")
