# Backend release workflow

`staging` → Render staging (`connect-full-backend.onrender.com`).
`main` → Render production (`connect-full-backend-production.onrender.com`).

## Promote staging to production

1. All work lands on `staging` first. Render deploys it to staging, and the QA scripts in `connect-customer-mobile/scripts/qa/` run against it.
2. Open or refresh the PR `staging` → `main`. CI must be green: backend-quality (pytest and semgrep), CodeQL and Sourcery.
3. Merge with **"Squash and merge"**. `main` and `staging` enforce linear history ("must not contain merge commits"), so squash is the only allowed method. The repo allows squash only.
4. After Render deploys, run the production checks:
   - `/live/`, `/health/` and `/readiness/` return 200
   - `/health/request-ip/` returns 404
   - `scripts/qa/password-reset-e2e.mjs production` and `scripts/qa/push-dispatch-proof.mjs production` both pass

## Re-align staging after every squash

A squash puts staging's content on `main` under a new commit. Reset `staging` to it, so the next PR starts clean:

```bash
git fetch origin
git diff --name-only origin/staging origin/main   # must print nothing
git checkout staging
git reset --hard origin/main
git push --force-with-lease origin staging
git push --force-with-lease origin staging:Staging
```

Only reset when that diff is empty. Otherwise `staging` has unreleased work: open a PR for it first. Never use `git merge` between the two branches, because merge commits are rejected.

## Branch names

`staging` and `Staging` both exist on GitHub and currently point at the same commit. Keep them identical (`git push origin staging:Staging`) until you've confirmed which one Render staging deploys from. Then delete the other one.

## Repository setting

GitHub → Settings → General → Pull Requests: **squash merging only**. Merge commits and rebase merging are off, which matches the linear-history rule on `main` and `staging`.
