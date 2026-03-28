import requests
import json
import random
import time
from datetime import datetime, timedelta

class ConnectLaundryQA:
    def __init__(self, base_url="http://localhost:8000/api/v1"):
        self.base_url = base_url
        self.tokens = {}
        self.l_id = None
        self.item_id = None
        self.category_id = None
        self.order_id = None
        self.password = "ConnectQA!Password2026"
        self.admin_creds = {"email": "admin@connect.com", "password": self.password}
        
    def log(self, action, success=True, message="", response=None):
        status = "[PASS]" if success else "[FAIL]"
        print(f"{status} {action}: {message}")
        if not success and response is not None:
            try:
                print(f"      Response ({response.status_code}): {json.dumps(response.json(), indent=2)}")
            except:
                print(f"      Response ({response.status_code}): {response.text[:1000]}")

    def run_request(self, method, endpoint, data=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        
        url = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        try:
            if method == "GET":
                return requests.get(url, headers=headers, params=data)
            elif method == "POST":
                return requests.post(url, headers=headers, data=json.dumps(data))
            elif method == "PATCH":
                return requests.patch(url, headers=headers, data=json.dumps(data))
            elif method == "PUT":
                return requests.put(url, headers=headers, data=json.dumps(data))
        except Exception as e:
            print(f"Request Error: {e}")
            return None

    def test_auth(self):
        print("--- AUTHENTICATION ---")
        res = self.run_request("POST", "/auth/login/", self.admin_creds)
        if res is not None and res.status_code == 200:
            self.tokens['admin'] = res.json()['data']['accessToken']
            self.log("Admin Login", True)
        else:
            self.log("Admin Login", False, "Failed", res)

        email = f"owner_{random.randint(1000, 9999)}@test.com"
        phone = "024" + "".join([str(random.randint(0, 9)) for _ in range(7)])
        owner_data = {
            "email": email, "password": self.password, "password_confirm": self.password,
            "first_name": "Test", "last_name": "Owner", "phone": phone, "role": "OWNER"
        }
        res = self.run_request("POST", "/auth/register/", owner_data)
        if res is not None and res.status_code == 201:
            self.tokens['owner'] = res.json()['data']['accessToken']
            self.log("Owner Registration", True, f"Email: {email}")

        email_c = f"customer_{random.randint(1000, 9999)}@test.com"
        phone_c = "055" + "".join([str(random.randint(0, 9)) for _ in range(7)])
        cust_data = owner_data.copy()
        cust_data.update({"email": email_c, "phone": phone_c, "role": "CUSTOMER"})
        res = self.run_request("POST", "/auth/register/", cust_data)
        if res is not None and res.status_code == 201:
            self.tokens['customer'] = res.json()['data']['accessToken']
            self.log("Customer Registration", True, f"Email: {email_c}")

    def test_catalog_discovery(self):
        print("--- CATALOG DISCOVERY ---")
        token = self.tokens.get('customer')
        if not token: return

        res = self.run_request("GET", "/booking/services/", None, token)
        if res is not None and res.status_code == 200:
            data = res.json().get('data', [])
            if data:
                self.category_id = data[0]['id']
                self.log("Found Category", True, f"ID: {self.category_id}")

        res = self.run_request("GET", "/booking/items/", None, token)
        if res is not None and res.status_code == 200:
            data = res.json().get('data', [])
            if data:
                self.item_id = data[0]['id']
                self.log("Found Item", True, f"ID: {self.item_id}")

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

        res = self.run_request("PATCH", f"/laundries/admin/laundries/{self.l_id}/approve/", {}, self.tokens['admin'])
        self.log("Admin Approval", res is not None and res.status_code == 200)

        if self.item_id and self.category_id:
            # Fixed payload: service_type instead of category
            service_data = {
                "laundry": self.l_id,
                "item": self.item_id,
                "service_type": self.category_id,
                "price": "15.00",
                "is_available": True
            }
            res = self.run_request("POST", "/laundries/admin/services/", service_data, self.tokens['admin'])
            if res is not None and res.status_code == 201:
                self.log("Service Added", True)
                update_data = {"pricing_methods": ["PER_KG", "PER_ITEM"]}
                res = self.run_request("PATCH", f"/laundries/dashboard/my-laundry/{self.l_id}/", update_data, self.tokens['owner'])
                self.log("Enabled PER_ITEM", res is not None and res.status_code == 200)
            else:
                self.log("Service Addition", False, f"Failed", res)

    def test_order_lifecycle(self):
        if not self.l_id or 'customer' not in self.tokens: return

        print("--- ORDER LIFECYCLE ---")
        order_data = {
            "laundry": self.l_id, "pricing_method": "PER_KG",
            "estimated_weight": 5.0, "pickup_date": (datetime.now() + timedelta(days=1)).isoformat(),
            "pickup_address": "Test Location", "delivery_address": "Test Location", "payment_method": "MOMO"
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
    tester = ConnectLaundryQA()
    tester.test_auth()
    tester.test_catalog_discovery()
    tester.test_laundry_management()
    time.sleep(1)
    tester.test_order_lifecycle()
