import requests
import json

url = "http://localhost:8000/api/v1/login/"
data = {"email": "admin@connect.com", "password": "adminpassword123"}
res = requests.post(url, json=data)
print(f"Status: {res.status_code}")
try:
    print(f"JSON: {json.dumps(res.json(), indent=2)}")
except Exception as e:
    print(f"Error: {e}")
    print(f"RAW: {res.text}")
