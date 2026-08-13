# Simame RC1 Release Manifest

Freeze date: 2026-08-13

This manifest records the verified RC1 implementation commits. The backend
repository also contains a later documentation-only commit that adds this
manifest; use the pushed branch HEAD reported by Git as the authoritative
backend repository SHA.

## Backend

- Repository: `connect_new_backend`
- Branch: `release/simame-rc1`
- Verified implementation SHA: `f06ae4dc82fcadd076be128d35f8f1fd8587da28`
- Full suite: 710 passed, 1 skipped, 14 warnings
- Focused COD/payment suite: 49 passed, 1 skipped
- Django system check: 0 issues
- Migration generation: no model drift
- OpenAPI: 0 errors, 3 enum naming warnings
- Database state: the two RC1 migrations remain unapplied to the configured database

## Customer Mobile

- Repository: `connect-customer-mobile`
- Branch: `release/simame-rc1`
- Exact SHA: `acbf88b92fc0d639f8a38f5155eadecfef29ded2`
- TypeScript: passed
- Jest: 31 suites, 269 tests passed
- Lint: 0 errors, 49 warnings
- Release configuration validation: passed with 15 absent production-environment warnings
- Expo dependencies: up to date
- Expo Doctor: 18/18 passed

## Owner Web

- Repository: `Connect-Web-App`
- Branch: `release/simame-rc1`
- Exact SHA: `19acb7f594e3bc7fb7ad7c078c1f3e4f9f61507c`
- TypeScript: passed
- Tests: 4 files, 8 tests passed
- Production build: passed; 25 static pages generated
- Lint: 0 errors, 181 warnings

## Approved Business Policy

- Cash on Delivery is supported.
- COD orders may be accepted before payment.
- COD remains financially unpaid until authorized cash collection.
- Custom-quote orders may be accepted before payment.
- Quote state and payment state are independent.
- Paystack/online orders cannot bypass online-payment requirements.
- Unpaid COD is excluded from Paystack settlement and available payout.
- Post-collection cash refunds remain a manual product-policy item unless separately approved.

## Pending Database Migrations

- `ordering.0018_order_payment_method`
- `payments.0012_payment_amount_collected_payment_collected_by_and_more`

**These migrations have NOT yet been applied to production. They must first be deployed and tested against the isolated staging environment.**

## Freeze Boundaries

No deployment, production migration, EAS production artifact, credential
rotation, real payment, or production infrastructure change was performed as
part of this freeze.
