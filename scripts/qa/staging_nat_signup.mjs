#!/usr/bin/env node
/**
 * Staging proof that shared-IP customers are not blocked, while per-identity
 * abuse still gets a human 429. Every request comes from this machine's one
 * public IP (a real shared-NAT scenario). All created accounts are deleted.
 *
 *   node scripts/qa/staging_nat_signup.mjs [--signups 25]
 */
import { randomUUID } from 'node:crypto';
import { writeFileSync } from 'node:fs';

const BASE = process.env.QA_API_URL ?? 'https://connect-full-backend.onrender.com/api/v1';
if (BASE.includes('production')) { console.error('Staging only.'); process.exit(2); }
const i = process.argv.indexOf('--signups');
const SIGNUPS = i > 0 ? Number(process.argv[i + 1]) : 25;

const results = [];
const record = (name, ok, detail = '') => {
  results.push({ name, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  — ${detail}` : ''}`);
};
const call = async (method, path, { token, body } = {}) => {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      'X-Device-ID': `qa-nat-${randomUUID()}`,
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  let json = null;
  try { json = await res.json(); } catch { /* non-JSON */ }
  return { status: res.status, json, retryAfter: res.headers.get('retry-after') };
};
const data = (r) => r.json?.data ?? r.json ?? {};

const stamp = Date.now();
const password = `Qa!${randomUUID().slice(0, 10)}Aa9`;
const accounts = [];

try {
  // 1. Many legitimate customers behind one public IP.
  const codes = [];
  for (let n = 0; n < SIGNUPS; n++) {
    const email = `qa.nat+${stamp}-${n}@simame-qa.test`;
    const r = await call('POST', '/auth/register/', { body: {
      email, password, password_confirm: password, first_name: 'QA', last_name: `Nat${n}`,
      phone: `2335${String(stamp + n).slice(-8)}`, role: 'CUSTOMER' } });
    codes.push(r.status);
    const d = data(r);
    if (d.accessToken) accounts.push({ email, access: d.accessToken, refresh: d.refreshToken });
  }
  record(`${SIGNUPS} distinct sign-ups from one shared IP`, codes.every((c) => c === 201),
    `201×${codes.filter((c) => c === 201).length}, other: ${codes.filter((c) => c !== 201).join(',') || 'none'} (old rule blocked customer 11)`);

  // 2. Hammering one email is stopped per address, with human copy.
  const target = accounts[0]?.email ?? `qa.nat+${stamp}-x@simame-qa.test`;
  let dup;
  const dupCodes = [];
  for (let n = 0; n < 6; n++) {
    dup = await call('POST', '/auth/register/', { body: {
      email: target, password, password_confirm: password, first_name: 'QA', last_name: 'Dup', phone: '233500000999' } });
    dupCodes.push(dup.status);
  }
  record('repeated sign-up on one email gets a human 429', dup.status === 429 &&
    /^We're receiving many sign-ups right now/.test(dup.json?.message ?? '') && Number(dup.retryAfter) > 0,
    `codes ${dupCodes.join(',')}; "${dup.json?.message}"; Retry-After ${dup.retryAfter}`);

  // 3. Normal customers continue: login + refresh work.
  const login = await call('POST', '/auth/login/', { body: { email: accounts[1].email, password } });
  record('normal login still works on the same IP', login.status === 200, `HTTP ${login.status}`);
  const refresh = await call('POST', '/auth/token/refresh/', { body: { refresh: data(login).refreshToken } });
  record('token refresh works (no false session expiry)', refresh.status === 200, `HTTP ${refresh.status}`);
  if (data(refresh).accessToken) accounts[1].access = data(refresh).accessToken;

  // 4. Reset emails: limited per address.
  let reset;
  const resetCodes = [];
  for (let n = 0; n < 4; n++) {
    reset = await call('POST', '/auth/forgot-password/', { body: { email: accounts[2].email } });
    resetCodes.push(reset.status);
  }
  record('4th reset email for one address gets a human 429', reset.status === 429 &&
    /^You can request another reset email in/.test(reset.json?.message ?? ''),
    `codes ${resetCodes.join(',')}; "${reset.json?.message}"`);
  const neighbour = await call('POST', '/auth/forgot-password/', { body: { email: accounts[3].email } });
  record('a neighbour on the same IP can still reset', neighbour.status === 200, `HTTP ${neighbour.status}`);

  // 5. Password guessing on one account is limited per account.
  const guessCodes = [];
  let guess;
  for (let n = 0; n < 11; n++) {
    guess = await call('POST', '/auth/login/', { body: { email: accounts[4].email, password: 'Wrong-password-1' } });
    guessCodes.push(guess.status);
  }
  record('password guessing on one account is stopped', guess.status === 429 &&
    /^Too many sign-in attempts/.test(guess.json?.message ?? ''), `codes ${guessCodes.join(',')}`);
  const other = await call('POST', '/auth/login/', { body: { email: accounts[5].email, password } });
  record('another customer on the same IP signs in normally', other.status === 200, `HTTP ${other.status}`);
} catch (error) {
  record('run aborted', false, String(error?.message ?? error));
} finally {
  let deleted = 0;
  const leftovers = [];
  for (const account of accounts) {
    const del = await call('DELETE', '/auth/account/', { token: account.access, body: { reason: 'qa_automation_cleanup' } });
    if (del.status < 300) deleted++; else leftovers.push(account.email);
  }
  record(`cleanup: deleted ${deleted}/${accounts.length} QA accounts`, leftovers.length === 0);
  if (leftovers.length) {
    writeFileSync(`${process.env.TEMP ?? '.'}/qa-nat-leftovers-${stamp}.json`, JSON.stringify({ password, leftovers }));
  }
  const failed = results.filter((r) => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} passed against ${BASE}`);
  process.exitCode = failed ? 1 : 0;
}
