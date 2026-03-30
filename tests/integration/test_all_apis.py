"""
Comprehensive Live API Test Script
Tests ALL Phase 1-4 endpoints against the running Django dev server.
"""
import json
import requests
import os
import sys
sys.stdout.reconfigure(encoding='utf-8')


BASE = "http://localhost:8000/api/v1"
RESULTS = []


def test(
        name,
        method,
        url,
        expected_status,
        data=None,
        headers=None,
        params=None):
    try:
        resp = getattr(
            requests,
            method)(
            url,
            json=data,
            headers=headers,
            params=params,
            timeout=30)
        passed = resp.status_code == expected_status
        try:
            body = resp.json()
        except Exception:
            body = resp.text[:200]
        RESULTS.append({"name": name,
                        "passed": passed,
                        "status": resp.status_code,
                        "expected": expected_status,
                        "body": str(body)[:300]})
        tag = "[PASS]" if passed else "[FAIL]"
        print(f"{tag} {name} -> {resp.status_code} (expected {expected_status})")
        if not passed:
            print(f"   Response: {str(body)[:300]}")
        return resp
    except Exception as e:
        RESULTS.append({"name": name,
                        "passed": False,
                        "status": "ERR",
                        "expected": expected_status,
                        "body": str(e)})
        print(f"[FAIL] {name} -> ERROR: {e}")
        return None


# ===== PHASE 1: AUTH =====
print("\n" + "=" * 60)
print("PHASE 1: AUTHENTICATION & ONBOARDING")
print("=" * 60)

resp = test(
    "Register OWNER",
    "post",
    f"{BASE}/auth/register/",
    201,
    data={
        "email": "owner_api_test2@laundry.com",
        "phone": "0551230002",
        "password": "StrongP@ss123!",
        "password_confirm": "StrongP@ss123!",
        "first_name": "Test",
        "last_name": "Owner",
        "role": "OWNER"})

resp = test(
    "Login OWNER",
    "post",
    f"{BASE}/auth/login/",
    200,
    data={
        "email": "owner_api_test2@laundry.com",
        "password": "StrongP@ss123!"})

TOKEN = None
if resp and resp.status_code == 200:
    body = resp.json()
    # The login view returns 'accessToken' directly
    TOKEN = body.get('accessToken') or body.get('access')
    if not TOKEN and 'data' in body:
        TOKEN = body['data'].get('access') or body['data'].get('accessToken')

if not TOKEN:
    print("\nCould not get JWT token. Response:")
    if resp:
        print(resp.json())
    sys.exit(1)

AUTH = {"Authorization": f"Bearer {TOKEN}"}
print(f"\nJWT Token: {TOKEN[:30]}...")

test("Get Profile", "get", f"{BASE}/auth/me/", 200, headers=AUTH)

# ===== PHASE 1b: STOREFRONT =====
print("\n" + "=" * 60)
print("PHASE 1b: STOREFRONT CREATION")
print("=" * 60)

resp = test("Create Laundry",
            "post",
            f"{BASE}/laundries/dashboard/my-laundry/",
            201,
            data={"name": "Sparkle Clean Laundry",
                  "description": "Premium in Accra",
                  "address": "15 Oxford St, Osu",
                  "city": "Accra",
                  "latitude": "5.5600",
                  "longitude": "-0.1870",
                  "phone_number": "0301234567",
                  "delivery_fee": "15.00",
                  "pickup_fee": "10.00",
                  "min_order": "30.00",
                  "opening_hours": [{"day": 1,
                                     "opening_time": "08:00",
                                     "closing_time": "18:00",
                                     "is_closed": False},
                                    {"day": 2,
                                     "opening_time": "08:00",
                                     "closing_time": "18:00",
                                     "is_closed": False},
                                    {"day": 7,
                                     "opening_time": "00:00",
                                     "closing_time": "00:00",
                                     "is_closed": True},
                                    ]},
            headers=AUTH)

LAUNDRY_ID = None
if resp and resp.status_code == 201:
    LAUNDRY_ID = resp.json().get('data', {}).get('id')
    print(f"   Laundry ID: {LAUNDRY_ID}")

test(
    "List My Laundries",
    "get",
    f"{BASE}/laundries/dashboard/my-laundry/",
    200,
    headers=AUTH)

if LAUNDRY_ID:
    test(
        "Update Laundry",
        "patch",
        f"{BASE}/laundries/dashboard/my-laundry/{LAUNDRY_ID}/",
        200,
        data={
            "description": "Updated: Best in Accra!"},
        headers=AUTH)
    test(
        "Get Hours",
        "get",
        f"{BASE}/laundries/dashboard/my-laundry/{LAUNDRY_ID}/hours/",
        200,
        headers=AUTH)
    test(
        "Toggle (not approved)",
        "patch",
        f"{BASE}/laundries/dashboard/my-laundry/{LAUNDRY_ID}/toggle/",
        400,
        headers=AUTH)
    test(
        "Owner Reviews",
        "get",
        f"{BASE}/laundries/dashboard/my-laundry/{LAUNDRY_ID}/reviews/",
        200,
        headers=AUTH)

# ===== PHASE 2: CATALOG =====
print("\n" + "=" * 60)
print("PHASE 2: CATALOG & PRICING")
print("=" * 60)

test(
    "List Categories",
    "get",
    f"{BASE}/laundries/categories/",
    200,
    headers=AUTH)
if LAUNDRY_ID:
    test(
        "Laundry Services",
        "get",
        f"{BASE}/laundries/laundries/{LAUNDRY_ID}/services/",
        200,
        headers=AUTH)

# ===== PHASE 3a: MACHINES =====
print("\n" + "=" * 60)
print("PHASE 3a: MACHINES MANAGEMENT")
print("=" * 60)

test(
    "List Machines (empty)",
    "get",
    f"{BASE}/laundries/dashboard/machines/",
    200,
    headers=AUTH)

resp = test(
    "Register Washer",
    "post",
    f"{BASE}/laundries/dashboard/machines/",
    201,
    data={
        "name": "Samsung Washer #1",
        "machine_type": "WASHER",
        "notes": "12kg"},
    headers=AUTH)
MACHINE_ID = resp.json().get('data', {}).get(
    'id') if resp and resp.status_code == 201 else None

test("Register Dryer", "post", f"{BASE}/laundries/dashboard/machines/", 201,
     data={"name": "LG Dryer #1", "machine_type": "DRYER"}, headers=AUTH)

test(
    "List Machines (2)",
    "get",
    f"{BASE}/laundries/dashboard/machines/",
    200,
    headers=AUTH)

if MACHINE_ID:
    test(
        "Status -> BUSY",
        "patch",
        f"{BASE}/laundries/dashboard/machines/{MACHINE_ID}/status/",
        200,
        data={
            "status": "BUSY"},
        headers=AUTH)
    test(
        "Update Notes",
        "patch",
        f"{BASE}/laundries/dashboard/machines/{MACHINE_ID}/",
        200,
        data={
            "notes": "Serviced 2026-03-25"},
        headers=AUTH)
    test(
        "Status -> MAINTENANCE",
        "patch",
        f"{BASE}/laundries/dashboard/machines/{MACHINE_ID}/status/",
        200,
        data={
            "status": "MAINTENANCE"},
        headers=AUTH)

# ===== PHASE 3b: STAFF =====
print("\n" + "=" * 60)
print("PHASE 3b: STAFF / TEAM")
print("=" * 60)

test(
    "List Staff (empty)",
    "get",
    f"{BASE}/laundries/dashboard/staff/",
    200,
    headers=AUTH)

resp = test(
    "Invite Washer",
    "post",
    f"{BASE}/laundries/dashboard/staff/invite/",
    201,
    data={
        "name": "Kofi Mensah",
        "email": "kofi@laundry.com",
        "phone": "0559876543",
        "role": "WASHER"},
    headers=AUTH)
STAFF_ID = resp.json().get('data', {}).get(
    'id') if resp and resp.status_code == 201 else None

test(
    "Invite Manager",
    "post",
    f"{BASE}/laundries/dashboard/staff/invite/",
    201,
    data={
        "name": "Ama Owusu",
        "email": "ama@laundry.com",
        "role": "MANAGER"},
    headers=AUTH)

test(
    "Duplicate Invite (fail)",
    "post",
    f"{BASE}/laundries/dashboard/staff/invite/",
    400,
    data={
        "name": "Kofi",
        "email": "kofi@laundry.com",
        "role": "WASHER"},
    headers=AUTH)

test(
    "List Staff (2)",
    "get",
    f"{BASE}/laundries/dashboard/staff/",
    200,
    headers=AUTH)

if STAFF_ID:
    test(
        "Change Role -> DRIVER",
        "patch",
        f"{BASE}/laundries/dashboard/staff/{STAFF_ID}/role/",
        200,
        data={
            "role": "DRIVER"},
        headers=AUTH)

# ===== PHASE 3c: CRM =====
print("\n" + "=" * 60)
print("PHASE 3c: CUSTOMER CRM")
print("=" * 60)

test(
    "Customer List",
    "get",
    f"{BASE}/laundries/dashboard/customers/",
    200,
    headers=AUTH)

# ===== PHASE 4a: ANALYTICS =====
print("\n" + "=" * 60)
print("PHASE 4a: DASHBOARD ANALYTICS")
print("=" * 60)

test(
    "Dashboard Stats",
    "get",
    f"{BASE}/laundries/dashboard/stats/",
    200,
    headers=AUTH)
resp = test(
    "Earnings + Time-Series + Sentiment",
    "get",
    f"{BASE}/laundries/dashboard/earnings/",
    200,
    headers=AUTH)
if resp and resp.status_code == 200:
    data = resp.json().get('data', {})
    has_ts = 'time_series' in data
    has_sent = 'sentiment' in data
    print(f"   time_series present: {has_ts}, sentiment present: {has_sent}")

# ===== PHASE 4b: PAYOUTS =====
print("\n" + "=" * 60)
print("PHASE 4b: PAYOUT CONTROLS")
print("=" * 60)

test(
    "List Bank Accounts (empty)",
    "get",
    f"{BASE}/payments/payouts/bank-account/",
    200,
    headers=AUTH)

resp = test(
    "Link Bank Account",
    "post",
    f"{BASE}/payments/payouts/bank-account/",
    201,
    data={
        "bank_name": "GCB Bank",
        "account_name": "Sparkle Ltd",
        "account_number": "1234567890",
        "bank_code": "040",
        "is_primary": True},
    headers=AUTH)

BANK_ID = resp.json().get('data', {}).get(
    'id') if resp and resp.status_code == 201 else None

test(
    "List Bank Accounts (1)",
    "get",
    f"{BASE}/payments/payouts/bank-account/",
    200,
    headers=AUTH)

if BANK_ID:
    test(
        "Payout (no balance)",
        "post",
        f"{BASE}/payments/payouts/request/",
        400,
        data={
            "bank_account_id": BANK_ID,
            "amount": "100.00"},
        headers=AUTH)

test(
    "Payout History",
    "get",
    f"{BASE}/payments/payouts/history/",
    200,
    headers=AUTH)

# ===== EXISTING ENDPOINTS =====
print("\n" + "=" * 60)
print("EXISTING: ORDERS & NOTIFICATIONS")
print("=" * 60)

test(
    "Dashboard Orders",
    "get",
    f"{BASE}/laundries/dashboard/orders/",
    200,
    headers=AUTH)
test(
    "Notifications",
    "get",
    f"{BASE}/support/notifications/",
    200,
    headers=AUTH)

# ===== SECURITY: UNAUTH =====
print("\n" + "=" * 60)
print("SECURITY: UNAUTHENTICATED (expect 401/403)")
print("=" * 60)

test("Unauth: Machines", "get", f"{BASE}/laundries/dashboard/machines/", 401)
test("Unauth: Staff", "get", f"{BASE}/laundries/dashboard/staff/", 401)
test("Unauth: Customers", "get", f"{BASE}/laundries/dashboard/customers/", 401)
test("Unauth: Payouts", "get", f"{BASE}/payments/payouts/history/", 401)
test("Unauth: Dashboard", "get", f"{BASE}/laundries/dashboard/stats/", 401)

# ===== CLEANUP =====
if MACHINE_ID:
    test(
        "Delete Machine",
        "delete",
        f"{BASE}/laundries/dashboard/machines/{MACHINE_ID}/",
        204,
        headers=AUTH)

# ===== REPORT =====
print("\n" + "=" * 60)
print("FINAL REPORT")
print("=" * 60)

passed = sum(1 for r in RESULTS if r['passed'])
failed = sum(1 for r in RESULTS if not r['passed'])
total = len(RESULTS)

print(f"\nTotal: {total} | Passed: {passed} | Failed: {failed}")
print(f"Pass Rate: {(passed / total * 100):.1f}%")

if failed > 0:
    print("\n--- FAILURES ---")
    for r in RESULTS:
        if not r['passed']:
            print(
                f"  [FAIL] {
                    r['name']}: got {
                    r['status']}, expected {
                    r['expected']}")
            print(f"     {r['body'][:200]}")

print("\nDone!")
