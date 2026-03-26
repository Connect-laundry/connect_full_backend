# AI Prompt: Build Connect Laundry Owner Dashboard (Production Rebuild)

> **Copy and paste this entire document as your prompt to any AI assistant (Cursor, Claude, ChatGPT, Gemini, etc.) to scaffold the frontend from scratch.**

---

## Your Mission

You are building the **Connect Laundry Owner Dashboard** — a production-grade web application for laundry business owners in Ghana to manage their entire business operations from a single interface.

The previous developer built a working prototype in Next.js with mock API routes. **We are rebuilding it from scratch** with:
1. **Industry-standard folder structure** — properly separated concerns, feature-based modules, not route-based clutter
2. **Real API integration only** — no mock backend, all calls go to the live Django/DRF server
3. **Scalable architecture** — the codebase must be maintainable as the app grows to 10x the current features

Do not carry over any `app/api/` mock routes. Do not use `json-server` or any mock data. Every piece of state must come from the real backend.

---

## Tech Stack (Non-Negotiable)

| Layer | Choice | Notes |
|-------|--------|-------|
| Framework | **Next.js 15** (App Router) | Use Server Components where possible |
| Language | **TypeScript** (strict mode) | All types must be explicit, no `any` |
| Styling | **Tailwind CSS v4** + **shadcn/ui** | Radix UI primitives |
| HTTP Client | **Axios** | Custom instance with interceptors |
| State | **Zustand** | For global auth + UI state |
| Server State | **TanStack Query v5** | All API calls must go through React Query |
| Forms | **React Hook Form** + **Zod** | Zod schemas must mirror backend validation |
| Icons | **Lucide React** | |
| Charts | **Recharts** | For revenue time-series and sentiment |
| Fonts | **Inter** (via `next/font/google`) | |

---

## Industry-Standard Folder Structure

Scaffold the project with this exact structure. No exceptions.

```
connect-dashboard/
├── src/
│   ├── app/                        # Next.js App Router (routing ONLY)
│   │   ├── (auth)/
│   │   │   ├── login/page.tsx
│   │   │   └── register/page.tsx
│   │   ├── (dashboard)/            # Protected layout group
│   │   │   ├── layout.tsx          # Auth guard + sidebar shell
│   │   │   ├── page.tsx            # Dashboard home (redirects to /overview)
│   │   │   ├── overview/page.tsx
│   │   │   ├── orders/
│   │   │   │   ├── page.tsx
│   │   │   │   └── [id]/page.tsx
│   │   │   ├── storefront/page.tsx
│   │   │   ├── services/page.tsx
│   │   │   ├── machines/page.tsx
│   │   │   ├── staff/page.tsx
│   │   │   ├── customers/
│   │   │   │   ├── page.tsx
│   │   │   │   └── [id]/page.tsx
│   │   │   ├── earnings/page.tsx
│   │   │   └── payouts/page.tsx
│   │   ├── layout.tsx              # Root layout (fonts, providers)
│   │   └── not-found.tsx
│   │
│   ├── features/                   # ★ HEART OF THE APP: Feature modules
│   │   ├── auth/
│   │   │   ├── components/         # LoginForm, RegisterForm
│   │   │   ├── hooks/              # useLogin, useRegister, useLogout
│   │   │   ├── schemas/            # loginSchema.ts, registerSchema.ts (Zod)
│   │   │   ├── services/           # auth.service.ts (API calls)
│   │   │   └── types.ts            # AuthUser, LoginPayload, RegisterPayload
│   │   ├── overview/
│   │   │   ├── components/         # StatCard, RecentOrders, RecentReviews, SentimentGauge
│   │   │   ├── hooks/              # useDashboardStats, useDashboardEarnings
│   │   │   └── services/           # dashboard.service.ts
│   │   ├── orders/
│   │   │   ├── components/         # OrdersTable, OrderDetail, LifecycleStepper, StatusBadge
│   │   │   ├── hooks/              # useOrders, useOrderLifecycle
│   │   │   ├── services/           # orders.service.ts, lifecycle.service.ts
│   │   │   └── types.ts
│   │   ├── storefront/
│   │   │   ├── components/         # StorefrontForm, HoursEditor, StoreToggle
│   │   │   ├── hooks/              # useStorefront, useToggleStore
│   │   │   ├── services/           # storefront.service.ts
│   │   │   └── schemas/            # storefrontSchema.ts
│   │   ├── machines/
│   │   │   ├── components/         # MachineCard, MachineForm, StatusBadge
│   │   │   ├── hooks/              # useMachines, useUpdateMachineStatus
│   │   │   ├── services/           # machines.service.ts
│   │   │   └── types.ts
│   │   ├── staff/
│   │   │   ├── components/         # StaffTable, InviteForm, RoleSelector
│   │   │   ├── hooks/              # useStaff, useInviteStaff
│   │   │   ├── services/           # staff.service.ts
│   │   │   └── types.ts
│   │   ├── customers/
│   │   │   ├── components/         # CustomerTable, CustomerProfile, SpendBadge
│   │   │   ├── hooks/              # useCustomers, useCustomerProfile
│   │   │   └── services/           # customers.service.ts
│   │   ├── earnings/
│   │   │   ├── components/         # RevenueChart, EarningsCards, SentimentMeter
│   │   │   ├── hooks/              # useEarnings
│   │   │   └── services/           # earnings.service.ts
│   │   └── payouts/
│   │       ├── components/         # PayoutForm, BankAccountCard, PayoutHistory
│   │       ├── hooks/              # usePayouts, useBankAccounts, useRequestPayout
│   │       ├── schemas/            # payoutSchema.ts
│   │       └── services/           # payouts.service.ts
│   │
│   ├── components/                 # Shared, reusable UI components
│   │   ├── ui/                     # shadcn/ui auto-generated (DO NOT EDIT)
│   │   ├── layout/
│   │   │   ├── Sidebar.tsx
│   │   │   ├── TopBar.tsx
│   │   │   └── PageHeader.tsx
│   │   └── shared/
│   │       ├── DataTable.tsx       # Generic paginated table
│   │       ├── ConfirmDialog.tsx
│   │       ├── EmptyState.tsx
│   │       ├── LoadingSpinner.tsx
│   │       └── StatusBadge.tsx     # Reusable status pill
│   │
│   ├── lib/                        # Pure utilities and configuration
│   │   ├── api/
│   │   │   ├── axios.ts            # Axios instance + interceptors
│   │   │   └── endpoints.ts        # All API URL constants
│   │   ├── store/
│   │   │   ├── auth.store.ts       # Zustand: user, tokens
│   │   │   └── ui.store.ts         # Zustand: sidebar open, modals
│   │   ├── providers/
│   │   │   └── AppProviders.tsx    # QueryClientProvider + Toaster
│   │   ├── utils/
│   │   │   ├── format.ts           # Currency, date, number formatters
│   │   │   └── cn.ts               # clsx + tailwind-merge helper
│   │   └── constants.ts            # QUERY_KEYS, POLL_INTERVAL, etc.
│   │
│   └── types/                      # Global TypeScript types
│       ├── api.types.ts            # Generic: ApiResponse<T>, PaginatedResponse<T>
│       └── index.ts                # Re-exports
│
├── public/
│   └── images/
├── .env.local                      # NEXT_PUBLIC_API_URL
├── .env.example
├── next.config.ts
├── tailwind.config.ts
└── tsconfig.json
```

---

## Core Architecture Rules

1. **Pages are thin.** `app/(dashboard)/orders/page.tsx` should only import and render a feature component. No logic in page files.
2. **Feature services are pure functions.** Every `services/*.service.ts` exports `async` functions that call `axiosInstance` and return typed data. No React hooks inside services.
3. **Hooks wrap React Query.** Every `hooks/use*.ts` wraps a service call with `useQuery` or `useMutation`. Hooks handle loading/error state.
4. **Zustand only for auth + global UI.** Server state (orders, stats, etc.) lives in React Query cache, not Zustand.
5. **Zod schemas are the source of truth for forms.** `react-hook-form` resolver must always use a Zod schema.

---

## API Configuration

### Environment Variables
```env
# .env.local
NEXT_PUBLIC_API_URL=https://connect-full-backend.onrender.com/api/v1
```

### Axios Instance (`src/lib/api/axios.ts`)

Build an Axios instance that:
1. Uses `NEXT_PUBLIC_API_URL` as `baseURL`
2. Sets `Content-Type: application/json`
3. **Request interceptor**: Reads `accessToken` from Zustand store and attaches `Authorization: Bearer <token>`
4. **Response interceptor**: On `401` → silence-refresh using `refreshToken` → retry original request → on refresh failure, call `useAuthStore.getState().logout()` and redirect to `/login`

```typescript
// Pattern to follow:
const api = axios.create({ baseURL: process.env.NEXT_PUBLIC_API_URL });

api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken;
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (res) => res,
  async (error) => {
    if (error.response?.status === 401 && !error.config._retry) {
      error.config._retry = true;
      // attempt token refresh...
    }
    return Promise.reject(error);
  }
);
```

### API Endpoints (`src/lib/api/endpoints.ts`)

```typescript
export const ENDPOINTS = {
  AUTH: {
    REGISTER: '/auth/register/',
    LOGIN: '/auth/login/',
    LOGOUT: '/auth/logout/',
    REFRESH: '/auth/token/refresh/',
    ME: '/auth/me/',
  },
  STOREFRONT: {
    LIST: '/laundries/dashboard/my-laundry/',
    DETAIL: (id: string) => `/laundries/dashboard/my-laundry/${id}/`,
    HOURS: (id: string) => `/laundries/dashboard/my-laundry/${id}/hours/`,
    TOGGLE: (id: string) => `/laundries/dashboard/my-laundry/${id}/toggle/`,
    REVIEWS: (id: string) => `/laundries/dashboard/my-laundry/${id}/reviews/`,
  },
  DASHBOARD: {
    STATS: '/laundries/dashboard/stats/',
    EARNINGS: '/laundries/dashboard/earnings/',
    ORDERS: '/laundries/dashboard/orders/',
    SERVICES: (id: string) => `/laundries/dashboard/services/${id}/`,
  },
  LIFECYCLE: {
    ACCEPT: (id: string) => `/booking/lifecycle/${id}/accept/`,
    REJECT: (id: string) => `/booking/lifecycle/${id}/reject/`,
    MARK_PICKED_UP: (id: string) => `/booking/lifecycle/${id}/mark-picked-up/`,
    MARK_WASHED: (id: string) => `/booking/lifecycle/${id}/mark-washed/`,
    MARK_OUT_FOR_DELIVERY: (id: string) => `/booking/lifecycle/${id}/mark-out-for-delivery/`,
    MARK_DELIVERED: (id: string) => `/booking/lifecycle/${id}/mark-delivered/`,
    COMPLETE: (id: string) => `/booking/lifecycle/${id}/complete/`,
    CANCEL: (id: string) => `/booking/lifecycle/${id}/cancel/`,
    TIMELINE: (id: string) => `/booking/lifecycle/${id}/timeline/`,
  },
  MACHINES: {
    LIST: '/laundries/dashboard/machines/',
    DETAIL: (id: string) => `/laundries/dashboard/machines/${id}/`,
    STATUS: (id: string) => `/laundries/dashboard/machines/${id}/status/`,
  },
  STAFF: {
    LIST: '/laundries/dashboard/staff/',
    INVITE: '/laundries/dashboard/staff/invite/',
    ROLE: (id: string) => `/laundries/dashboard/staff/${id}/role/`,
    DETAIL: (id: string) => `/laundries/dashboard/staff/${id}/`,
  },
  CUSTOMERS: {
    LIST: '/laundries/dashboard/customers/',
    PROFILE: (id: string) => `/laundries/dashboard/customers/${id}/profile/`,
  },
  PAYOUTS: {
    BANK_ACCOUNTS: '/payments/payouts/bank-account/',
    REQUEST: '/payments/payouts/request/',
    HISTORY: '/payments/payouts/history/',
  },
  NOTIFICATIONS: '/support/notifications/',
} as const;
```

---

## Complete TypeScript Types

Define these in `src/types/api.types.ts` and per-feature `types.ts` files:

```typescript
// Generic wrappers
export interface ApiResponse<T> {
  status: 'success' | 'error';
  message?: string;
  data: T;
}
export interface PaginatedResponse<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

// Auth
export interface AuthUser {
  id: string;
  email: string;
  fullName: string;
  role: 'OWNER' | 'CUSTOMER' | 'ADMIN';
}
export interface AuthTokens {
  accessToken: string;
  refreshToken: string;
}

// Laundry / Storefront
export type LaundryStatus = 'PENDING' | 'APPROVED' | 'REJECTED';
export type PriceRange = '$' | '$$' | '$$$';
export interface OpeningHour {
  id: string;
  day: 1 | 2 | 3 | 4 | 5 | 6 | 7; // 1=Monday … 7=Sunday
  dayDisplay: string;
  opening_time: string;
  closing_time: string;
  is_closed: boolean;
}
export interface Laundry {
  id: string;
  name: string;
  description: string;
  image: string | null;
  imageUrl: string | null;
  address: string;
  city: string;
  latitude: string;
  longitude: string;
  phone_number: string;
  price_range: PriceRange;
  estimated_delivery_hours: number;
  delivery_fee: string;
  pickup_fee: string;
  min_order: string;
  is_featured: boolean;
  is_active: boolean;
  status: LaundryStatus;
  statusDisplay: string;
  opening_hours: OpeningHour[];
  created_at: string;
  updated_at: string;
}

// Orders
export type OrderStatus =
  | 'PENDING' | 'CONFIRMED' | 'PICKED_UP'
  | 'IN_PROCESS' | 'OUT_FOR_DELIVERY' | 'DELIVERED'
  | 'COMPLETED' | 'CANCELLED' | 'REJECTED';

export interface DashboardOrder {
  id: string;
  order_no: string;
  customer_name: string;
  status: OrderStatus;
  status_display: string;
  total_amount: string;
  created_at: string;
  pickup_date: string | null;
  delivery_date: string | null;
}

// Dashboard Stats
export interface DashboardStats {
  pending_count: number;
  confirmed_count: number;
  picked_up_count: number;
  delivered_count: number;
  total_orders: number;
  recent_orders: DashboardOrder[];
  recent_reviews: Review[];
}

// Earnings
export interface TimeSeriesPoint { date: string; revenue: string; }
export interface SentimentData {
  total_reviews: number;
  positive_reviews: number;
  score: number | null;
}
export interface Earnings {
  today: string;
  this_week: string;
  this_month: string;
  total_revenue: string;
  time_series: TimeSeriesPoint[];
  sentiment: SentimentData;
}

// Machines
export type MachineType = 'WASHER' | 'DRYER' | 'IRONER' | 'OTHER';
export type MachineStatus = 'IDLE' | 'BUSY' | 'MAINTENANCE' | 'OUT_OF_ORDER';
export interface Machine {
  id: string;
  name: string;
  machine_type: MachineType;
  typeDisplay: string;
  status: MachineStatus;
  statusDisplay: string;
  notes: string;
  created_at: string;
  updated_at: string;
}

// Staff
export type StaffRole = 'MANAGER' | 'WASHER' | 'IRONER' | 'DRIVER' | 'RECEPTIONIST';
export type InviteStatus = 'PENDING' | 'ACCEPTED' | 'DECLINED';
export interface StaffMember {
  id: string;
  name: string;
  email: string;
  phone: string;
  role: StaffRole;
  roleDisplay: string;
  invite_status: InviteStatus;
  inviteStatusDisplay: string;
  created_at: string;
  updated_at: string;
}

// Customers (CRM)
export interface CustomerSummary {
  user_id: string;
  email: string;
  first_name: string;
  last_name: string;
  phone: string;
  order_count: number;
  total_spent: string;
  last_order_date: string;
}
export interface CustomerOrder {
  order_no: string;
  status: OrderStatus;
  total_amount: string;
  created_at: string;
}
export interface CustomerProfile extends CustomerSummary {
  orders: CustomerOrder[];
}

// Reviews
export interface Review {
  id: string;
  userName: string;
  rating: number;
  comment: string;
  date: string;
}

// Payouts
export interface BankAccount {
  id: string;
  bank_name: string;
  account_name: string;
  account_number: string;
  bank_code: string;
  is_primary: boolean;
  created_at: string;
}
export type PayoutStatus = 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED';
export interface PayoutRequest {
  id: string;
  bank_account: string;
  bank_account_display: string;
  amount: string;
  currency: string;
  status: PayoutStatus;
  reference: string;
  notes: string;
  requested_at: string;
  processed_at: string | null;
}
```

---

## Zustand Auth Store

```typescript
// src/lib/store/auth.store.ts
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null;
  user: AuthUser | null;
  isAuthenticated: boolean;
  setTokens: (tokens: AuthTokens & { user: AuthUser }) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      refreshToken: null,
      user: null,
      isAuthenticated: false,
      setTokens: ({ accessToken, refreshToken, user }) =>
        set({ accessToken, refreshToken, user, isAuthenticated: true }),
      logout: () =>
        set({ accessToken: null, refreshToken: null, user: null, isAuthenticated: false }),
    }),
    { name: 'connect-auth' }
  )
);
```

---

## React Query Keys

```typescript
// src/lib/constants.ts
export const QUERY_KEYS = {
  STOREFRONT: ['storefront'],
  STATS: ['dashboard', 'stats'],
  EARNINGS: ['dashboard', 'earnings'],
  ORDERS: (params?: object) => ['dashboard', 'orders', params],
  MACHINES: ['machines'],
  STAFF: ['staff'],
  CUSTOMERS: ['customers'],
  CUSTOMER_PROFILE: (id: string) => ['customers', id],
  PAYOUT_HISTORY: ['payouts', 'history'],
  BANK_ACCOUNTS: ['payouts', 'bank-accounts'],
  NOTIFICATIONS: ['notifications'],
} as const;

export const POLL_INTERVAL = 30_000; // 30 seconds for live order updates
```

---

## Backend API Behaviour (Critical Notes)

These facts come directly from the backend source code. The frontend MUST handle all of these:

### Auth
- **Login response key is `accessToken`** — NOT `access_token` or `token`. Key is camelCase.
- **Refresh endpoint** accepts `{ "refresh": "..." }` (lowercase), returns `{ "access": "..." }`.
- **Register auto-logs in** — the 201 response includes `accessToken` and `refreshToken` directly.
- **Password rules**: Min 8 chars, not purely numeric, not too common (Django validators).
- **django-axes**: After 5 failed logins from an IP, the endpoint returns `403`. Show specific locked message.

### OWNER Role Guard
- All dashboard endpoints check `user.role in ['OWNER', 'ADMIN']`. A `CUSTOMER` account will get `403` on every dashboard endpoint even with a valid token.

### Storefront
- **One laundry per owner** — `POST /dashboard/my-laundry/` returns `400` if laundry already exists.
- **New laundries start as `PENDING`** and `is_active = false`. The store toggle only works on `APPROVED` laundries. Show a "Pending Approval" banner until approved.
- **Opening hours days** use `1-7` (1=Monday, 7=Sunday). NOT 0-indexed.

### Orders
- Orders are **read-only from the owner dashboard** (`GET /dashboard/orders/` is `ReadOnlyModelViewSet`).
- Order status changes happen only via **lifecycle endpoints** under `/booking/lifecycle/{id}/`.
- Lifecycle actions enforce a **strict state machine** — invalid transitions return `400` with `{ "current_status": "...", "target_status": "..." }`.

### Machines & Staff
- Both require an existing laundry — `400` with `"You must create a laundry first."` if not.
- Staff invite is **email-idempotent per laundry** — duplicate email returns `400`.

### Payouts
- Available balance = `total_earned (DELIVERED+COMPLETED orders)` minus `total_paid_out (PENDING+PROCESSING+COMPLETED payouts)`.
- Always show the available balance in the UI before letting the owner enter an amount.
- Reference is auto-generated server-side in format `PO-XXXXXXXXXXXX`.

### Response Envelope
All owner/dashboard endpoints return:
```json
{ "status": "success", "message": "...", "data": { ... } }
```
Extract `.data` in your service layer before returning to hooks/components.

---

## Required Pages & Features

### `/overview` — Dashboard Home
- **4 stat cards**: Pending Orders, Confirmed Orders, Total Orders, Total Revenue (from `/stats/`)
- **Revenue cards row**: Today / This Week / This Month / All-Time (from `/earnings/`)
- **Revenue line chart**: 12-day time series from `earnings.time_series`
- **Sentiment gauge**: Circle or bar showing `earnings.sentiment.score` (null state = "No reviews yet")
- **Recent Orders table**: Last 5 orders with status badges (from `stats.recent_orders`)
- **Recent Reviews feed**: Last 5 reviews with star ratings (from `stats.recent_reviews`)
- **Auto-poll** every 30s

### `/orders` — Order Management
- Paginated, filterable table by `status` and `created_at`
- Search by `order_no` or `customer_name`
- Clicking a row opens an **Order Detail drawer/modal**
- Detail shows full lifecycle stepper with **action buttons** per current status
- Lifecycle stepper shows allowed transitions only:
  - PENDING → [Accept] [Reject]
  - CONFIRMED → [Mark Picked Up]
  - PICKED_UP → [Mark Washed]
  - IN_PROCESS → [Out for Delivery]
  - OUT_FOR_DELIVERY → [Mark Delivered]
  - DELIVERED → [Complete Order]

### `/storefront` — Business Profile
- Edit form: name, description, address, city, lat/lng, phone, delivery fee, pickup fee, min order, price range
- **Interactive map** (optional): latitude/longitude picker
- **Store Toggle switch**: ON/OFF. Disabled with tooltip if status is not APPROVED
- **Opening Hours editor**: Table with 7 rows (Mon-Sun), time pickers, and "Closed" toggle per day
- **Image upload** via `multipart/form-data`

### `/machines` — Equipment Inventory
- Grid of machine cards, color-coded by status:
  - `IDLE` → Green
  - `BUSY` → Yellow/Amber
  - `MAINTENANCE` → Orange
  - `OUT_OF_ORDER` → Red
- Each card has a quick **status dropdown** (Select + PATCH to `/status/`)
- "Add Machine" button opens a form modal
- Delete with confirmation dialog

### `/staff` — Team Management
- Table with columns: Name, Email, Phone, Role, Invite Status, Actions
- Role filter tabs: All / Manager / Washer / Driver / etc.
- "Invite Staff" button opens drawer with form
- Inline role change via select dropdown → PATCH to `/{id}/role/`
- Remove staff with confirmation

### `/customers` — CRM
- Sortable, searchable table sorted by `total_spent` descending
- Columns: Customer, Email, Phone, Orders, Total Spent, Last Order
- Click row → `CustomerProfile` page at `/customers/[id]`
- Profile page shows customer header info + full order history table

### `/earnings` — Analytics
- Repeats revenue cards from overview (standalone page)
- Full-width **Recharts `<LineChart>`** for the 12-day time series
- **Sentiment section**: Progress bar or radial chart showing positive review %
- **Reviews list** (paginated, from `/{laundry_id}/reviews/`)

### `/payouts` — Financial Hub
- **Available Balance card** (computed from earnings - paid out)
- **Bank Accounts section**: List cards + "Add Bank Account" form
- **Request Payout form**: Amount input (validated against balance), bank account selector, notes
- **Payout History table**: Reference, Amount, Status badge, Date

---

## Validation Schemas (Zod)

```typescript
// Example: src/features/auth/schemas/registerSchema.ts
import { z } from 'zod';

export const registerSchema = z.object({
  first_name: z.string().min(1, 'Required'),
  last_name: z.string().min(1, 'Required'),
  email: z.string().email('Invalid email'),
  phone: z.string().min(10, 'Invalid phone number'),
  password: z.string().min(8, 'Min 8 characters'),
  password_confirm: z.string(),
  role: z.literal('OWNER'),
}).refine((d) => d.password === d.password_confirm, {
  message: "Passwords don't match",
  path: ['password_confirm'],
});

// Payout schema
export const payoutSchema = z.object({
  bank_account_id: z.string().uuid(),
  amount: z.number().positive().refine(
    (v) => v <= MAX_BALANCE, // inject max from hook
    'Amount exceeds available balance'
  ),
  notes: z.string().optional(),
});
```

---

## UI/UX Design System

### Color Palette (Tailwind config)
```javascript
// tailwind.config.ts
colors: {
  brand: {
    50: '#f0f9ff',
    500: '#0ea5e9',  // Sky blue — primary CTA
    600: '#0284c7',
  },
  success: '#22c55e',
  warning: '#f59e0b',
  danger: '#ef4444',
  surface: '#f8fafc',
  'surface-elevated': '#ffffff',
}
```

### Status Badge Colors
| Status | Tailwind Classes |
|--------|-----------------|
| PENDING | `bg-amber-100 text-amber-800` |
| CONFIRMED | `bg-blue-100 text-blue-800` |
| PICKED_UP | `bg-indigo-100 text-indigo-800` |
| IN_PROCESS | `bg-purple-100 text-purple-800` |
| OUT_FOR_DELIVERY | `bg-cyan-100 text-cyan-800` |
| DELIVERED | `bg-green-100 text-green-800` |
| COMPLETED | `bg-emerald-100 text-emerald-800` |
| CANCELLED | `bg-gray-100 text-gray-800` |
| REJECTED | `bg-red-100 text-red-800` |

### Machine Status Colors
| Status | Color |
|--------|-------|
| IDLE | Green |
| BUSY | Amber |
| MAINTENANCE | Orange |
| OUT_OF_ORDER | Red |

### Typography
- Font: `Inter` via `next/font/google`
- Page titles: `text-2xl font-bold text-gray-900`
- Section headers: `text-lg font-semibold text-gray-800`
- Body: `text-sm text-gray-600`
- Monospace (order numbers): `font-mono text-xs`

---

## What to Scaffold First (Build Order)

Follow this order to unblock yourself:

1. **`src/lib/api/axios.ts`** — Axios instance (foundation for everything)
2. **`src/lib/store/auth.store.ts`** — Zustand auth store
3. **`src/lib/providers/AppProviders.tsx`** — Query client + toast
4. **`src/types/api.types.ts`** — All TypeScript types
5. **`src/lib/api/endpoints.ts`** — All endpoint constants
6. **`src/features/auth/`** — Login/Register pages + hooks
7. **`src/app/(dashboard)/layout.tsx`** — Auth guard + sidebar shell
8. **`src/features/overview/`** — Dashboard stats + earnings
9. **`src/features/orders/`** — Orders table + lifecycle
10. **`src/features/storefront/`** — Business profile editor
11. **`src/features/machines/`** — Equipment board
12. **`src/features/staff/`** — Team management
13. **`src/features/customers/`** — CRM
14. **`src/features/payouts/`** — Payouts + bank accounts
15. **`src/features/earnings/`** — Analytics standalone page

---

## Constraints & Anti-Patterns to Avoid

- ❌ No `any` types — use proper generics
- ❌ No API calls inside components — all calls go through service functions → hooks
- ❌ No `useEffect` + `fetch` — use React Query `useQuery` / `useMutation`
- ❌ No mock data — if an endpoint is unavailable in development, use React Query's `placeholderData`
- ❌ No hardcoded strings — use `ENDPOINTS` constants
- ❌ No token logic inside components — token management is Axios interceptor + Zustand only
- ❌ No `app/api/` routes — this is a pure API consumer, not a full-stack app
- ✅ Always extract `.data` from the response envelope in the service layer
- ✅ Always handle loading and error states in every page
- ✅ Use `toast` (Sonner/shadcn) for all mutation success/error feedback

---

## Getting Started Commands

```bash
npx create-next-app@latest connect-dashboard \
  --typescript \
  --tailwind \
  --app \
  --src-dir \
  --import-alias "@/*"

cd connect-dashboard

# Install dependencies
npm install axios zustand @tanstack/react-query react-hook-form zod @hookform/resolvers recharts lucide-react sonner

# Install shadcn/ui
npx shadcn@latest init
npx shadcn@latest add button card input label dialog sheet select badge table skeleton toast separator tabs

# Create .env.local
echo "NEXT_PUBLIC_API_URL=https://connect-full-backend.onrender.com/api/v1" > .env.local
```

---

*This prompt was generated from the production backend codebase. All type definitions, enum values, and API shapes are derived directly from the Django/DRF source code and have been live-tested at 97.5% pass rate. — March 2026*
