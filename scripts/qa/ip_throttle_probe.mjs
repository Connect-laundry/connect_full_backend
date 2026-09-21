#!/usr/bin/env node
/**
 * Black-box proof of how the backend identifies a client for IP throttles.
 * Low volume, staging only. Uses non-existent emails so only the per-IP
 * login scope can trigger.
 *
 *   node scripts/qa/ip_throttle_probe.mjs [--requests 14] [--phase plain|spoof|both]
 */
import { randomUUID } from 'node:crypto';

const BASE = process.env.QA_API_URL ?? 'https://connect-full-backend.onrender.com/api/v1';
if (BASE.includes('production')) { console.error('Staging only.'); process.exit(2); }
const arg = (name, fallback) => { const i = process.argv.indexOf(`--${name}`); return i > 0 ? process.argv[i + 1] : fallback; };
const N = Number(arg('requests', 14));
const PHASE = arg('phase', 'both');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const attempt = async (headers = {}) => {
  const res = await fetch(`${BASE}/auth/login/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify({ email: `nobody+${randomUUID()}@simame-qa.test`, password: 'Wrong-password-1' }),
  });
  return { status: res.status, retryAfter: res.headers.get('retry-after') };
};

const burst = async (label, headersFor) => {
  const statuses = [];
  let firstLimited = null; let retryAfter = null;
  for (let i = 1; i <= N; i++) {
    const r = await attempt(headersFor(i));
    statuses.push(r.status);
    if (r.status === 429 && firstLimited === null) { firstLimited = i; retryAfter = r.retryAfter; }
  }
  console.log(`${label}: statuses=${statuses.join(',')} first429=${firstLimited ?? 'none'} retryAfter=${retryAfter ?? '-'}`);
  return firstLimited;
};

if (PHASE === 'plain' || PHASE === 'both') {
  await burst('A plain (same real IP)', () => ({}));
}
if (PHASE === 'both') await sleep(65000);
if (PHASE === 'spoof' || PHASE === 'both') {
  const hit = await burst('B spoofed X-Forwarded-For per request', (i) => ({ 'X-Forwarded-For': `203.0.113.${i}` }));
  console.log(hit === null
    ? 'VERDICT: spoofing X-Forwarded-For bypasses the per-IP limit'
    : 'VERDICT: forged X-Forwarded-For is ignored (limit still applied)');
}
