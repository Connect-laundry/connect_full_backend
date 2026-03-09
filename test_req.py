import urllib.request
import urllib.error
import json

urls = [
    'http://127.0.0.1:8000/api/v1/support/home/special-offers/',
    'http://127.0.0.1:8000/api/v1/laundries/laundries/?is_featured=true'
]

for url in urls:
    try:
        req = urllib.request.Request(url, headers={'Authorization': 'Bearer bad_token_12345'})
        with urllib.request.urlopen(req) as response:
            print(f"Success {url}:", response.status)
            print(response.read().decode())
    except urllib.error.HTTPError as e:
        print(f"HTTPError {url}:", e.code)
        print(e.read().decode())
    except Exception as e:
        print(f"Error {url}:", e)
