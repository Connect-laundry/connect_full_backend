import requests
import json
import logging
import uuid
import sys
import time

# Use 127.0.0.1 to avoid Windows localhost/IPv6 issues
BASE_URL = "http://127.0.0.1:8000/api/v1"
ADMIN_CREDENTIALS = {"email": "testadmin100@example.com", "password": "testpassword123"}

# Global constants from DB seed
ST_ID = "cf76367a-fb24-4233-b86d-8d90b38b2202"  # Wash & Iron
ITEM_ID = "6164ce16-1a7f-4a9e-9abf-372b83d4b5c6"  # Shirt

logging.basicConfig(level=logging.INFO, format="%(message)s")

results = {"total_tests": 0, "passed": 0, "failed": 0, "tests": []}

store = {
    "admin_token": None,
    "owner_token": None,
    "customer_token": None,
    "laundry_id": None,
}


class CriticalTestFailure(Exception):
    pass


def log_test(test_name, passed, details=""):
    results["total_tests"] += 1
    if passed:
        results["passed"] += 1
    else:
        results["failed"] += 1

    res_obj = {
        "test": test_name,
        "status": "PASS" if passed else "FAIL",
        "details": details,
    }
    results["tests"].append(res_obj)
    logging.info(json.dumps(res_obj, indent=2))

    if not passed:
        raise CriticalTestFailure(f"Critical Failure: {test_name}")


def assert_status(response, expected_status, test_name):
    passed = (
        isinstance(expected_status, list) and response.status_code in expected_status
    ) or (
        not isinstance(expected_status, list)
        and response.status_code == expected_status
    )
    details = f"Expected {expected_status}, got {
        response.status_code}. Response: {
        response.text[
            :200]}"
    log_test(test_name, passed, details)
    return passed


def run_tests():
    owner_email = f"owner_{uuid.uuid4().hex[:6]}@test.com"
    customer_email = f"customer_{uuid.uuid4().hex[:6]}@test.com"

    logging.info(f"Starting Quality Certification at {
            time.strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        # STEP 1: Admin Setup
        logging.info("\n--- STEP 1: Admin Setup ---")
        r_admin = requests.post(
            f"{BASE_URL}/auth/login/", json=ADMIN_CREDENTIALS, timeout=30
        )
        assert_status(r_admin, 200, "1.1 Admin Authentication")
        store["admin_token"] = r_admin.json().get("data", {}).get("accessToken")
        a_client = requests.Session()
        a_client.headers.update({"Authorization": f"Bearer {store['admin_token']}"})

        # STEP 2: User Onboarding
        logging.info("\n--- STEP 2: User Onboarding ---")
        phone_o = f"024{uuid.uuid4().hex[:7]}"[:10]
        r1 = requests.post(
            f"{BASE_URL}/auth/register/",
            json={
                "email": owner_email,
                "password": "Password123!",
                "password_confirm": "Password123!",
                "first_name": "Test",
                "last_name": "Owner",
                "phone": phone_o,
                "role": "OWNER",
            },
            timeout=30,
        )
        assert_status(r1, 201, "2.1 Owner Registration")
        store["owner_token"] = r1.json().get("data", {}).get("accessToken")

        phone_c = f"025{uuid.uuid4().hex[:7]}"[:10]
        r2 = requests.post(
            f"{BASE_URL}/auth/register/",
            json={
                "email": customer_email,
                "password": "Password123!",
                "password_confirm": "Password123!",
                "first_name": "Test",
                "last_name": "Customer",
                "phone": phone_c,
            },
            timeout=30,
        )
        assert_status(r2, 201, "2.2 Customer Registration")
        store["customer_token"] = r2.json().get("data", {}).get("accessToken")

        o_client = requests.Session()
        o_client.headers.update({"Authorization": f"Bearer {store['owner_token']}"})
        c_client = requests.Session()
        c_client.headers.update({"Authorization": f"Bearer {store['customer_token']}"})

        # STEP 3: Laundry Lifecycle
        logging.info("\n--- STEP 3: Laundry Lifecycle ---")
        r3 = o_client.post(
            f"{BASE_URL}/laundries/dashboard/my-laundry/",
            json={
                "name": f"QA_Cert_{uuid.uuid4().hex[:4]}",
                "address": "123 Test St",
                "city": "Accra",
                "latitude": 5.6037,
                "longitude": -0.1870,
                "phone_number": "0240000003",
                "pricing_methods": ["PER_ITEM", "PER_KG"],
                "price_per_kg": 15.00,
            },
        )
        assert_status(r3, 201, "3.1 Laundry Profile Creation")
        store["laundry_id"] = r3.json().get("data", {}).get("id")

        # Admin Approval
        r4 = a_client.patch(
            f"{BASE_URL}/laundries/admin/laundries/{store['laundry_id']}/approve/"
        )
        assert_status(r4, 200, "3.2 Admin Approval")

        # STEP 4: Configuration
        logging.info("\n--- STEP 4: Configuration ---")
        # Hours
        r5 = o_client.put(
            f"{BASE_URL}/laundries/dashboard/my-laundry/{store['laundry_id']}/hours/",
            json=[
                {"day": i, "opening_time": "08:00:00", "closing_time": "18:00:00"}
                for i in range(1, 8)
            ],
        )
        assert_status(r5, 200, "4.1 Set Operating Hours")

        # Add Service Item (to allow PER_ITEM activation)
        r_item = o_client.post(
            f"{BASE_URL}/laundries/dashboard/my-laundry/{
                store['laundry_id']}/services/",
            json={"item_id": ITEM_ID, "service_type_id": ST_ID, "price": 10.00},
        )
        assert_status(r_item, 201, "4.2 Added Service Pricing (PER_ITEM)")

        # STEP 5: Activation
        logging.info("\n--- STEP 5: Activation ---")
        r6 = o_client.patch(
            f"{BASE_URL}/laundries/dashboard/my-laundry/{store['laundry_id']}/activate/"
        )
        assert_status(r6, 200, "5.1 Successful Activation")

        # STEP 6: Order - PER_KG
        logging.info("\n--- STEP 6: Ordering - PER_KG ---")
        r7 = c_client.post(
            f"{BASE_URL}/booking/create/",
            json={
                "laundry": store["laundry_id"],
                "pickup_date": "2026-10-10T10:00:00Z",
                "pickup_address": "Test Street 10",
                "pricing_method": "PER_KG",
                "estimated_weight": 5.0,
            },
        )
        assert_status(r7, 201, "6.1 Place Order (PER_KG)")
        order_id_kg = r7.json().get("data", {}).get("id")

        # STEP 7: Order - PER_ITEM
        logging.info("\n--- STEP 7: Ordering - PER_ITEM ---")
        r8 = c_client.post(
            f"{BASE_URL}/booking/create/",
            json={
                "laundry": store["laundry_id"],
                "pickup_date": "2026-10-11T10:00:00Z",
                "pickup_address": "Test Street 11",
                "pricing_method": "PER_ITEM",
                "items": [{"item": ITEM_ID, "service_type": ST_ID, "quantity": 2}],
            },
        )
        assert_status(r8, 201, "7.1 Place Order (PER_ITEM)")
        order_id_item = r8.json().get("data", {}).get("id")

        # STEP 8: Security
        logging.info("\n--- STEP 8: Security Boundaries ---")
        if order_id_kg:
            r9 = c_client.patch(
                f"{BASE_URL}/orders/{order_id_kg}/update-weight/",
                json={"actual_weight": 4.5},
            )
            assert_status(r9, 403, "8.1 Security: Customer Cannot Update Weight")

        # Final certification check
        logging.info("\nFinal assessment of results...")

    except CriticalTestFailure as ctf:
        logging.error(f"TEST EXECUTION HALTED: {str(ctf)}")
    except Exception as e:
        logging.error(f"UNEXPECTED ERROR: {str(e)}")
        results["failed"] += 1

    # Summary
    logging.info("\n" + "=" * 40)
    logging.info("       FINAL QA CERTIFICATION        ")
    logging.info("=" * 40)
    logging.info(
        json.dumps(
            {
                "total": results["total_tests"],
                "passed": results["passed"],
                "failed": results["failed"],
            },
            indent=2,
        )
    )

    with open("qa_validation_report.json", "w") as f:
        json.dump(results, f, indent=2)

    if results["failed"] > 0:
        logging.error("\n[!!] DEPLOYMENT BLOCKED")
        sys.exit(1)
    else:
        logging.info("\n[OK] DEPLOYMENT APPROVED")
        sys.exit(0)


if __name__ == "__main__":
    run_tests()
