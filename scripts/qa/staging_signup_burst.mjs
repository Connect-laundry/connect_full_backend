#!/usr/bin/env node
/** Staging only: fire N rapid sign-ups from this IP, report where 429 starts
 * and its Retry-After, then delete every account created. */
import { randomUUID } from 'node:crypto';

const BASE = 'https://connect-full-backend.onrender.com/api/v1';
const N = Number(process.argv[2] ?? 62);
const stamp = Date.now();
const password = `Qa!${randomUUID().slice(0, 10)}Aa9`;
const tokens = [];
const rows = [];
for (let n = 0; n < N; n++) {
  const r = await fetch(`${BASE}/auth/register/`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: `qa.burst+${stamp}-${n}@simame-qa.test`, password, password_confirm: password,
      first_name: 'QA', last_name: `Burst${n}`, phone: `2334${String(stamp + n).slice(-8)}` }),
  });
  const j = await r.json().catch(() => ({}));
  if (j?.data?.accessToken) tokens.push(j.data.accessToken);
  rows.push({ n: n + 1, status: r.status, retryAfter: r.headers.get('retry-after'), message: r.status === 429 ? j.message : '' });
}
const first429 = rows.find((r) => r.status === 429);
console.log(`created ${tokens.length}/${N}; first 429 at request ${first429?.n ?? 'none'}; Retry-After ${first429?.retryAfter ?? '-'}; "${first429?.message ?? ''}"`);
console.log('statuses:', rows.map((r) => r.status).join(','));
let deleted = 0;
for (const t of tokens) {
  const d = await fetch(`${BASE}/auth/account/`, { method: 'DELETE', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${t}` }, body: JSON.stringify({ reason: 'qa_automation_cleanup' }) });
  if (d.status < 300) deleted++;
}
console.log(`cleanup: deleted ${deleted}/${tokens.length}`);
