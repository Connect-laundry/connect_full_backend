# Backend release workflow

`staging` → Render staging (`connect-full-backend.onrender.com`).
`main` → Render production (`connect-full-backend-production.onrender.com`).

## Promote staging to production

1. All work lands on `staging` first. Render deploys it to staging, and the QA scripts in `connect-customer-mobile/scripts/qa/` run against it.
2. Open or refresh the PR `staging` → `main`. CI must be green: backend-quality (pytest and semgrep), CodeQL and Sourcery.
3. Merge with **"Create a merge commit"**. **Do not squash or rebase.**
   - A squash creates a new commit that `staging` never contains. The branches then diverge, and the next PR reports conflicts in every file both sides touched (seen with #180, #181, #183 and #184).
4. After Render deploys, run the production checks:
   - `/live/`, `/health/` and `/readiness/` return 200
   - `/health/request-ip/` returns 404
   - `scripts/qa/password-reset-e2e.mjs production` and `scripts/qa/push-dispatch-proof.mjs production` both pass

## If a squash merge happened anyway

`main` then holds staging's content under an unrelated commit. Re-align without changing any file:

```bash
git fetch origin
git diff --name-only <staging-sha-that-was-merged> origin/main   # must print nothing
git checkout staging
git merge -s ours origin/main -m "Merge main into staging (squash re-alignment)"
git push origin staging
```

Only use `-s ours` when that diff is empty. Otherwise `main` has real changes, and they must be merged normally.

## Branch names

`staging` and `Staging` both exist on GitHub and currently point at the same commit. Keep them identical (`git push origin staging:Staging`) until you've confirmed which one Render staging deploys from. Then delete the other one.

## Repository setting (recommended)

GitHub → Settings → General → Pull Requests: turn off **Allow squash merging** and **Allow rebase merging**, and leave **Allow merge commits** on. This stops accidental squashes of `staging` → `main`.
