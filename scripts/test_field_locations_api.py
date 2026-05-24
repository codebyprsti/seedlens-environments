"""
Quick test for GET /api/v1/field-locations.
Run with: python scripts/test_field_locations_api.py
Ensure the FastAPI server is running (e.g. uvicorn main:app --host 127.0.0.1 --port 8019).
"""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

try:
    import requests
except ImportError:
    print("Install requests: pip install requests")
    sys.exit(1)

BASE = "http://127.0.0.1:8019"
URL = f"{BASE}/api/v1/field-locations"

def main():
    print(f"GET {URL}")
    try:
        r = requests.get(URL, timeout=10)
    except requests.exceptions.ConnectionError:
        print("Connection failed. Is the server running? (e.g. uvicorn main:app --port 8019)")
        sys.exit(1)
    print(f"Status: {r.status_code}")
    if r.status_code != 200:
        print(f"Body: {r.text[:500]}")
        sys.exit(1)
    j = r.json()
    assert "data" in j, "Missing 'data' key"
    assert "columns" in j, "Missing 'columns' key"
    data = j["data"]
    columns = j["columns"]
    print(f"data: {len(data)} rows")
    print(f"columns: {len(columns)} column definitions")
    if columns:
        print("Column names:", [c.get("db_column_name") for c in columns])
    if data:
        print("First row keys:", list(data[0].keys()))
        print("Sample row:", data[0])
    print("OK: field-locations API response structure is valid.")

if __name__ == "__main__":
    main()
