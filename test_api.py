import os, json, requests
from dotenv import load_dotenv

load_dotenv()
KEY = os.getenv("CRUSTDATA_API_KEY")

resp = requests.post(
    "https://api.crustdata.com/company/enrich",
    headers={
        "authorization": f"Bearer {KEY}",
        "x-api-version": "2025-11-01",
        "content-type": "application/json",
    },
    json={"domains": ["retool.com"]},
)

print("Status:", resp.status_code)
os.makedirs("fixtures", exist_ok=True)
with open("fixtures/retool_enrich.json", "w") as f:
    json.dump(resp.json(), f, indent=2)
print(json.dumps(resp.json(), indent=2)[:3000])

