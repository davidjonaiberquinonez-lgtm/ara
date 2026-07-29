import requests
r = requests.get("http://localhost:5000/api/reporte/pdf",
                 params={"fecha_inicio": "2026-07-14", "fecha_fin": "2026-07-20"})
print("Status:", r.status_code, "| Content-Type:",
      r.headers.get("Content-Type"), "| Bytes:", len(r.content))
print("OK PDF" if r.status_code == 200 and r.headers.get("Content-Type", "").startswith("application/pdf") else "FAIL")
