# 07 — Security, Privacy & Ghana Regulatory Compliance

## 1. Ghana Legal & Regulatory Framework

SIMAME's SMS notification system is designed to adhere to Ghanaian regulatory standards:

1. **Data Protection Act, 2012 (Act 843)**:
   - **Principle of Data Minimization (Section 21)**: SMS messages are broadcast across unencrypted telecom operator channels (SS7 / GSM). Therefore, customer personal data in the SMS body is minimized. The default `compact` template masks the customer phone number (`024 XXX 1234`) and shortens delivery addresses to landmarks, directing the owner to the authenticated Seller Center for complete details.
   - **Right to Purpose Limitation (Section 22)**: Owner phone numbers collected for transactional booking notifications are strictly segregated from marketing lists. Registering for booking alerts does NOT enroll the owner into promotional broadcasts.
   - **Security of Personal Data (Section 28)**: In the database, frozen order snapshots containing customer names, phones, and addresses are automatically scrubbed after 30 days via `services.maintenance.purge_old_payloads()`.
2. **National Communications Authority (NCA) Guidelines**:
   - All SMS messages use an approved, registered alphanumeric Sender ID (`SIMAME`).
   - Transactional messages are strictly differentiated from promotional messages (no marketing copy, promo codes, or sales pitches are embedded in operational booking alerts).

---

## 2. Threat Modeling & Mitigation Analysis

### 2.1 Cross-Tenant Data Leakage (IDOR)
- **Threat**: A malicious laundry merchant attempts to view or hijack notifications of a competitor by guessing order IDs or laundry UUIDs.
- **Mitigation**:
  - All dashboard endpoints resolve `Laundry.objects.filter(owner=request.user).first()`.
  - The client is never trusted to supply a `laundry_id`.
  - Deliveries are partitioned by `laundry_id`. Even in admin and monitoring logs, recipient phone numbers are masked (`+233 24 *** 1234`).

### 2.2 SMS Pumping & Abuse Prevention
- **Threat**: Fraudulent actors attempt to use the OTP verification or "Send Test SMS" endpoints to generate unpaid SMS traffic (SMS pumping fraud).
- **Mitigation**:
  - Authentication required: Anonymous users cannot invoke verification or test SMS.
  - Role check: Only authenticated users with `role == 'OWNER'` possessing an approved laundry profile can call endpoints.
  - Strict Rate Limiting:
    - OTP verification: Max 3 requests per laundry per hour (`MERCHANT_SMS_OTP_HOURLY_LIMIT = 3`).
    - Test SMS: Max 3 requests per laundry per day (`MERCHANT_SMS_TEST_DAILY_LIMIT = 3`).
    - Monitored and audited via `MerchantSmsSendLog`.
  - Phone validation: Numbers must be valid Ghanaian mobile prefixes (MTN, Telecel, AT). International roaming destinations are rejected.

### 2.3 Webhook Forgery & Tampering
- **Threat**: An attacker attempts to forge Arkesel delivery status webhooks to falsely mark messages as delivered or failed.
- **Mitigation**:
  - Secret URL Token: Delivery callbacks require an unguessable high-entropy token in the path: `/api/v1/notifications/arkesel-sms-status/<token>/`.
  - Unknown Message Rejection: Callbacks for unrecognized `sms_id` values return HTTP 200 without modifying any database records.
  - Non-Mutation of Financial/Order Data: Delivery webhooks ONLY update `AdminNotificationDelivery` status. Under zero circumstances can a delivery callback mutate an order's status, inventory, or payment state.

### 2.4 Credential Exposure
- **Threat**: Leaking the Arkesel API key to frontend clients or mobile bundles.
- **Mitigation**:
  - `ARKESEL_API_KEY` is exclusively consumed server-side in `connect_new_backend/admin_notifications/providers/arkesel_sms.py`.
  - Zero reference to Arkesel API keys exists in `Connect-Web-App` or `connect-customer-mobile`.
  - Backend configuration checks verify that no production API keys match dummy patterns or forbidden staging origins.
