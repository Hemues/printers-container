import urllib.request, json

data = json.dumps({"username": "admin", "password": "admin"}).encode()
req = urllib.request.Request(
    "http://localhost:8082/api/login",
    data=data,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    r = urllib.request.urlopen(req)
    print(r.read().decode())
except Exception as e:
    print(str(e))
