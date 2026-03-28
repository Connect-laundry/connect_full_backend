import requests
import json
import time
import random
import sys
from datetime import datetime, timedelta

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)

class ConnectLaundryQA:
    def __init__(self, base_url="http://127.0.0.1:8000/api/v1"):
        self.base_url = base_url
        self.tokens = {}
        self.l_id = None
        self.order_id = None
        self.category_id = None
        self.item_id = None

    def log(self, step, success, message="", res=None):
        status = "[PASS]" if success else "[FAIL]"
        print(f"{status} {step}: {message}")
        if not success and res is not None:
            try:
                print(f"      Response ({res.status_code}): {json.dumps(res.json(), indent=2)}")
            except:
                print(f"      Response ({res.status_code}): {res.text[:500]}")
        sys.stdout.flush()

    def run_request(self, method, endpoint, data=None, token=None):
        url = f"{self.base_url}{endpoint}"
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        
        try:
            if method == "POST":
                res = requests.post(url, headers=headers, data=json.dumps(data), timeout=30)
            elif method == "GET":
                res = requests.get(url, headers=headers, timeout=30)
            elif method == "PATCH":
                res = requests.patch(url, headers=headers, data=json.dumps(data), timeout=30)
            elif method == "PUT":
                res = requests.put(url, headers=headers, data=json.dumps(data), timeout=30)
            
            # Debug: print every request
            # print(f"DEBUG: {method} {endpoint} -> {res.status_code}")
            return res
        except Exception as e:
            print(f"Request Error ({method} {endpoint}): {e}")
            return None

    def test_auth(self):
        print("--- AUTHENTICATION ---")
        # Admin Login
        admin_data = {"email": "admin@connect.com", "password": "adminpassword123"}
        res = self.run_request("POST", "/auth/login/", admin_data)
        if res is not None and res.status_code == 200:
            self.tokens['admin'] = res.json()['data']['access']
            self.log("Admin Login", True)
        else:
            self.log("Admin Login", False, "Failed to login as admin", res)
            return False
        
        # Owner Reg & Login
        email_o = f"owner_{random.randint(10000, 99999)}@test.com"
        owner_data = {"email": email_o, "password": "password123", "first_name": "QA", "last_name": "Owner", "role": "LAUNDRY_OWNER"}
        res = self.run_request("POST", "/auth/register/", owner_data)
        if res is not None and res.status_code == 201:
            res = self.run_request("POST", "/auth/login/", {"email": email_o, "password": "password123"})
            if res and res.status_code == 200:
                self.tokens['owner'] = res.json()['data']['access']
                self.log("Owner Registration", True, f"Email: {email_o}")
            else:
                self.log("Owner Login", False, "Failed", res)
        else:
            self.log("Owner Registration", False, "Failed", res)

        # Customer Reg & Login
        email_c = f"customer_{random.randint(10000, 99999)}@test.com"
        cust_data = {"email": email_c, "password": "password123", "first_name": "QA", "last_name": "Customer", "role": "CUSTOMER"}
        res = self.run_request("POST", "/auth/register/", cust_data)
        if res is not None and res.status_code == 201:
            res = self.run_request("POST", "/auth/login/", {"email": email_c, "password": "password123"})
            if res and res.status_code == 200:
                self.tokens['customer'] = res.json()['data']['access']
                self.log("Customer Registration", True, f"Email: {email_c}")
            else:
                self.log("Customer Login", False, "Failed", res)
        else:
            self.log("Customer Registration", False, "Failed", res)
        return True

    def test_catalog_discovery(self):
        if 'admin' not in self.tokens: return
        token = self.tokens['admin']
        
        print("--- CATALOG DISCOVERY ---")
        # 1. Ensure we have a Service Type category
        cat_data = {"name": f"Test Service {random.randint(100,999)}", "type": "SERVICE_TYPE"}
        res = self.run_request("POST", "/laundries/admin/categories/", cat_data, token)
        if res is not None and res.status_code in [201, 400]:
            if res.status_code == 201:
                self.category_id = res.json()['data']['id']
            else: # Find existing
                categories = self.run_request("GET", "/booking/services/", None, token).json().get('data', [])
                if categories: self.category_id = categories[0]['id']
        self.log("Service Type Ready", hasattr(self, 'category_id'))

        # 2. Ensure we have a Launderable Item
        item_data = {"name": f"Test Item {random.randint(100,999)}"}
        res = self.run_request("POST", "/laundries/admin/items/", item_data, token)
        if res is not None and res.status_code in [201, 400]:
            if res.status_code == 201:
                self.item_id = res.json()['data']['id']
            else: # Find existing
                items = self.run_request("GET", "/booking/items/", None, token).json().get('data', [])
                if items: self.item_id = items[0]['id']
        self.log("Item Ready", hasattr(self, 'item_id'))

    def test_laundry_management(self):
        if 'owner' not in self.tokens: return
            
        print("--- LAUNDRY MANAGEMENT ---")
        laundry_data = {
            "name": f"QA Laundry {random.randint(100, 999)}",
            "address": "123 QA Street",
            "phone_number": "020" + "".join([str(random.randint(0, 9)) for _ in range(7)]),
            "latitude": 5.6037, "longitude": -0.1870,
            "pricing_methods": ["PER_KG"], "price_per_kg": "10.00"
        }
        res = self.run_request("POST", "/laundries/dashboard/my-laundry/", laundry_data, self.tokens['owner'])
        if res is not None and res.status_code in [200, 201]:
            self.l_id = res.json()['data']['id']
            self.log("Create Laundry", True, f"ID: {self.l_id}")
        else:
            self.log("Create Laundry", False, "Failed", res)
            return

        # Approve
        res = self.run_request("PATCH", f"/laundries/admin/laundries/{self.l_id}/approve/", {}, self.tokens['admin'])
        self.log("Admin Approval", res is not None and res.status_code == 200)

        # Link Service
        if hasattr(self, 'item_id') and hasattr(self, 'category_id'):
            service_data = {
                "laundry": self.l_id,
                "item": self.item_id,
                "service_type": self.category_id,
                "price": "15.00",
                "is_available": True
            }
            res = self.run_request("POST", "/laundries/admin/services/", service_data, self.tokens['admin'])
            self.log("Service Linked", res is not None and res.status_code in [201, 400])
            
            # Enable methods
            update_data = {"pricing_methods": ["PER_KG", "PER_ITEM"]}
            res = self.run_request("PATCH", f"/laundries/dashboard/my-laundry/{self.l_id}/", update_data, self.tokens['owner'])
            self.log("Pricing Methods Enabled", res is not None and res.status_code == 200)

    def test_order_lifecycle(self):
        if not hasattr(self, 'l_id') or 'customer' not in self.tokens: return

        print("--- ORDER LIFECYCLE ---")
        order_data = {
            "laundry": self.l_id, "pricing_method": "PER_KG",
            "estimated_weight": 5.0, "pickup_date": (datetime.now() + timedelta(days=1)).isoformat(),
            "pickup_address": "Test Location", "delivery_address": "Test Location", "payment_method": "MOMO",
            "items": [
                {
                    "item": self.item_id,
                    "service_type": self.category_id,
                    "quantity": 2
                }
            ]
        }
        res = self.run_request("POST", "/booking/create/", order_data, self.tokens['customer'])
        if res is not None and res.status_code in [200, 201]:
            resp_json = res.json()
            self.order_id = resp_json.get('data', {}).get('id') or resp_json.get('id')
            self.log("Place Order", True, f"ID: {self.order_id}")
        else:
            self.log("Place Order", False, "Failed", res)
            return

        weight_data = {"actual_weight": 6.5}
        res = self.run_request("PATCH", f"/orders/{self.order_id}/update-weight/", weight_data, self.tokens['owner'])
        if res is not None and res.status_code == 200:
            data = res.json().get('data', res.json())
            self.log("Update Weight", True, f"Final Price: {data.get('final_price')}, Status: {data.get('status')}")
        else:
            self.log("Update Weight", False, "Failed", res)

        res = self.run_request("GET", f"/orders/{self.order_id}/", None, self.tokens['customer'])
        if res is not None and res.status_code == 200:
            data = res.json().get('data', res.json())
            self.log("Verify Final Status", True, f"Status: {data.get('status')}")

if __name__ == "__main__":
    try:
        tester = ConnectLaundryQA()
        if tester.test_auth():
            tester.test_catalog_discovery()
            tester.test_laundry_management()
            time.sleep(1)
            tester.test_order_lifecycle()
    except Exception as e:
        print(f"CRITICAL TEST ERROR: {e}")
        import traceback
        traceback.print_exc()
