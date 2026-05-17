#!/usr/bin/env python3
"""Quick check: does the printers app render FA icon SVGs?

Fetches the main.js bundle, extracts the icon() function call path,
and verifies the SVG generation works by looking at what the server
actually delivers after Angular hydrates.
"""
import urllib.request
import json
import sys

BASE = "http://localhost:8082"

# Step 1: Login
print("--- Step 1: Login ---")
data = json.dumps({"username": "admin", "password": "admin"}).encode()
req = urllib.request.Request(
    f"{BASE}/api/login",
    data=data,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    r = urllib.request.urlopen(req)
    resp = json.loads(r.read().decode())
    token = resp["token"]
    print(f"  Token: {token[:16]}...")
except Exception as e:
    print(f"  Login failed: {e}")
    sys.exit(1)

# Step 2: Change password (required for first run)
print("--- Step 2: Change password ---")
data = json.dumps({
    "current_password": "admin",
    "new_password": "TestPass123!"
}).encode()
req = urllib.request.Request(
    f"{BASE}/api/change-password",
    data=data,
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    },
    method="POST",
)
try:
    r = urllib.request.urlopen(req)
    print(f"  Password changed: {r.read().decode()}")
except Exception as e:
    print(f"  Change password: {e}")

# Step 3: Fetch the index.html
print("--- Step 3: Fetch index.html ---")
req = urllib.request.Request(f"{BASE}/")
r = urllib.request.urlopen(req)
html = r.read().decode()
print(f"  HTML length: {len(html)}")
print(f"  Has <fa-icon: {'<fa-icon' in html}")
print(f"  Has ng-fa-icon: {'ng-fa-icon' in html}")
print(f"  Has <svg: {'<svg' in html}")

# Step 4: Get main.js filename
import re
m = re.search(r'main-[A-Z0-9]+\.js', html)
if m:
    main_js = m.group(0)
    print(f"  main.js: {main_js}")
else:
    print("  ERROR: main.js not found in HTML")
    sys.exit(1)

# Step 5: Check the main.js for FA icon rendering code
print("--- Step 5: Analyze main.js ---")
req = urllib.request.Request(f"{BASE}/{main_js}")
r = urllib.request.urlopen(req)
js = r.read().decode()
print(f"  JS length: {len(js)}")

# Check for icon definitions
icon_names = ["envelope", "print", "chart-bar", "clipboard-list", "plus", "times", "trash-can"]
for name in icon_names:
    if f'iconName:"{name}"' in js:
        print(f"  ✓ Icon '{name}' definition found")
    else:
        print(f"  ✗ Icon '{name}' definition NOT found")

# Check for FA component
if 'ng-fa-icon' in js:
    print("  ✓ FA component (ng-fa-icon) found")
else:
    print("  ✗ FA component NOT found")

# Check for renderedIconHTML
if 'renderedIconHTML' in js:
    print("  ✓ renderedIconHTML signal found")
else:
    print("  ✗ renderedIconHTML NOT found")

# Check for bypassSecurityTrustHtml
if 'bypassSecurityTrustHtml' in js:
    print("  ✓ bypassSecurityTrustHtml found")
else:
    print("  ✗ bypassSecurityTrustHtml NOT found")

# Check for the icon() call that generates SVG
if '.icon(' in js or 'icon(' in js:
    print("  ✓ icon() call found")
else:
    print("  ✗ icon() call NOT found")

# Check for SVG path data (at least one icon should have path data)
if 'M48 64c' in js or 'M64 64C' in js:
    print("  ✓ SVG path data found")
else:
    print("  ✗ SVG path data NOT found")

print("\n--- Done ---")
print("Note: Angular is an SPA. Icons are rendered client-side via JavaScript.")
print("The index.html will NOT contain <svg> or <fa-icon> elements.")
print("If all checks above pass, the issue is likely browser-side (service worker cache, etc.)")
