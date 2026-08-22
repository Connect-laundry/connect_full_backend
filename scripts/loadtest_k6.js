import http from 'k6/http';
import { check, sleep } from 'k6';

// k6 Load Test Configuration for KNUST Launch
export const options = {
  scenarios: {
    // 1. Baseline: Normal traffic (50 users over 5 mins)
    baseline: {
      executor: 'constant-vus',
      vus: 50,
      duration: '5m',
      tags: { test_type: 'baseline' },
      startTime: '0s',
    },
    // 2. KNUST Launch Load: 250 concurrent users
    launch_load: {
      executor: 'ramping-vus',
      startVUs: 10,
      stages: [
        { duration: '2m', target: 100 },
        { duration: '5m', target: 250 },
        { duration: '2m', target: 50 },
      ],
      tags: { test_type: 'load' },
      startTime: '6m',
    },
    // 3. Marketing Spike: 500 users within 30s
    campus_spike: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 500 },
        { duration: '2m', target: 500 },
        { duration: '30s', target: 0 },
      ],
      tags: { test_type: 'spike' },
      startTime: '16m',
    },
    // 4. Stress: Ramp to 1000 users to test breaking threshold
    stress_test: {
      executor: 'ramping-vus',
      startVUs: 100,
      stages: [
        { duration: '2m', target: 400 },
        { duration: '2m', target: 800 },
        { duration: '2m', target: 1200 },
        { duration: '1m', target: 0 },
      ],
      tags: { test_type: 'stress' },
      startTime: '20m',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<500', 'p(99)<1200'], // 95% of requests must complete below 500ms
    http_req_failed: ['rate<0.01'],                  // HTTP failure rate under 1%
  },
};

const TEST_PROFILE = __ENV.TEST_PROFILE || 'smoke';
const selectedProfiles = {
  smoke: {
    executor: 'constant-vus',
    vus: 1,
    duration: '30s',
    tags: { test_type: 'smoke' },
  },
  baseline: options.scenarios.baseline,
  load: options.scenarios.launch_load,
  spike: options.scenarios.campus_spike,
  stress1000: options.scenarios.stress_test,
  soak: {
    executor: 'constant-vus',
    vus: 100,
    duration: '2h',
    tags: { test_type: 'soak' },
  },
  burst: {
    executor: 'ramping-vus',
    startVUs: 0,
    stages: [
      { duration: '10s', target: 300 },
      { duration: '20s', target: 300 },
      { duration: '10s', target: 0 },
    ],
    tags: { test_type: 'burst' },
  },
};

if (!selectedProfiles[TEST_PROFILE]) {
  throw new Error(
    `Unknown TEST_PROFILE "${TEST_PROFILE}". Use smoke, baseline, load, spike, stress1000, soak, or burst.`,
  );
}

options.scenarios = {
  [TEST_PROFILE]: { ...selectedProfiles[TEST_PROFILE], startTime: '0s' },
};

const BASE_URL = (__ENV.API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');

export function setup() {
  const isLocal = /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/i.test(BASE_URL);
  const productionHost = /connect-full-backend-production\.onrender\.com/i.test(BASE_URL);
  if (productionHost) {
    throw new Error('Production load testing is blocked by this harness.');
  }
  if (!isLocal && __ENV.SIMAME_LOAD_TEST_ACK !== 'I_HAVE_STAGING_AUTHORIZATION') {
    throw new Error(
      'Remote load testing requires written staging authorization and ' +
      'SIMAME_LOAD_TEST_ACK=I_HAVE_STAGING_AUTHORIZATION.',
    );
  }
  return { target: BASE_URL };
}

export default function () {
  // 1. Health / Readiness check
  let healthRes = http.get(`${BASE_URL}/readiness/`);
  check(healthRes, {
    'health check status is 200': (r) => r.status === 200,
  });

  // 2. Discover Laundries
  let laundriesRes = http.get(`${BASE_URL}/api/v1/laundries/?limit=10`);
  check(laundriesRes, {
    'laundries list status is 200': (r) => r.status === 200,
  });

  sleep(Math.random() * 2 + 1);

  // 3. Price Estimation
  let estimatePayload = JSON.stringify({
    laundry: '00000000-0000-0000-0000-000000000001',
    pickup_lat: 6.6745,
    pickup_lng: -1.5716,
    items: [],
  });

  let estimateRes = http.post(`${BASE_URL}/api/v1/booking/estimate/`, estimatePayload, {
    headers: { 'Content-Type': 'application/json' },
  });

  check(estimateRes, {
    'estimate response is valid': (r) => r.status === 200 || r.status === 400 || r.status === 401,
  });

  sleep(Math.random() * 3 + 1);
}
