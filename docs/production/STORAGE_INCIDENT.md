# Simame Storage Incident Runbook

Date accessed for source guidance: 2026-08-10.

## Current Project Signals

`connect_new_backend/config/settings.py` configures Cloudinary from `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, and `CLOUDINARY_API_SECRET`, with local media fallback in DEBUG. Image and file upload paths exist for avatars, laundries, price imports, and user media.

## Production Rules

- Optional media upload failure must not corrupt unrelated business records.
- Required upload failure must return a controlled 4xx/5xx response with user-safe text.
- Storage credentials must never be shipped in Expo public variables.
- Large, invalid, corrupted, and unsupported files must be rejected deterministically.

## Incident Triage

1. Determine whether failure is credential, provider outage, timeout, file validation, URL generation, or deletion drift.
2. Pause user flows that create irreversible bad records if uploads are required.
3. Preserve original request IDs and affected object keys.
4. Retry only idempotent upload/delete operations.
5. Reconcile database rows whose media URL points to missing provider objects.

## Stop Conditions

Stop release if owner registration fails because optional logo upload failed, customer avatar upload can crash the app, required media silently disappears, or production lacks Cloudinary credentials.