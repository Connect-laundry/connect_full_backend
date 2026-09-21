#!/usr/bin/env node
/** Staging only. A bot floods empty sign-ups from this IP; afterwards a real
 * customer on the same IP must still be able to sign up immediately, because
 * rejected requests no longer consume the shared burst/hourly/daily budget. */
import { randomUUID } from 'node:crypto';

const BASE = 'https://connect-full-backend.onrender.com/api/v1';
const FLOOD = Number(process.argv[2] ?? 320);
const post = (body) => fetch(`${BASE}/auth/register/`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

const counts = {};
for (let n = 0; n < FLOOD; n++) {
  const r = await post({});
  counts[r.status] = (counts[r.status] ?? 0) + 1;
}
console.log(`flood of ${FLOOD} empty sign-ups:`, JSON.stringify(counts));

const stamp = Date.now();
const password = `Qa!${randomUUID().slice(0, 10)}Aa9`;
const real = await post({ email: `qa.neighbour+${stamp}@simame-qa.test`, password, password_confirm: password,
  first_name: 'QA', last_name: 'Neighbour', phone: `2337${String(stamp).slice(-8)}` });
const body = await real.json().catch(() => ({}));
console.log(`real neighbour sign-up right after the flood: HTTP ${real.status} ${real.status === 201 ? 'PASS' : 'FAIL'} ${body.message ?? ''}`);
const token = body?.data?.accessToken;
if (token) {
  const d = await fetch(`${BASE}/auth/account/`, { method: 'DELETE', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify({ reason: 'qa_automation_cleanup' }) });
  console.log(`cleanup: HTTP ${d.status}`);
}
process.exitCode = real.status === 201 ? 0 : 1;
