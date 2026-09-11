# Grove API — Python + Supabase backend

FastAPI backend for the PGI Job Portal login page. Handles:

- Student sign up (self-service)
- Student login
- Company login (accounts are provisioned by an admin, not self-signup)
- Company account provisioning (admin-only, secret-protected)

## 1. Set up Supabase

1. Create a project at [supabase.com](https://supabase.com).
2. Go to **Project Settings → API** and copy:
   - `Project URL` → `SUPABASE_URL`
   - `anon public` key → `SUPABASE_ANON_KEY`
   - `service_role` key → `SUPABASE_SERVICE_ROLE_KEY` (keep this secret — never send it to the browser)
3. Go to **SQL Editor** and run these files in order:
   - `schema.sql` — creates the `profiles` table (role, name/college or company name).
   - `schema_dashboard.sql` — adds extra profile fields, plus `companies` and `drives` tables for the dashboard.
   - `schema_assessment.sql` — adds `assessment_status`/`qualified_domain` to `profiles`, plus the `assessment_attempts` log used by the technical-assessment engine.
4. Go to **Authentication → Providers → Email** and, for local testing, you can turn off "Confirm email" so signups work instantly without needing to click an email link (turn it back on for production).

## 2. Set up the backend

```bash
cd grove-backend
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# now edit .env and paste in your Supabase URL + keys

uvicorn app:app --reload --port 8000
```

The API is now running at `http://localhost:8000`. Check it with:

```bash
curl http://localhost:8000/api/health
```

## 3. Connect the frontend

In `login.html`, the `API_BASE` constant already points at
`http://localhost:8000` when you open the page from `localhost`. When you
deploy the backend (Render, Railway, Fly.io, etc.), update the fallback URL
in that same constant to your deployed backend's address.

## 4. Endpoints

| Method | Path | Who calls it | Description |
|---|---|---|---|
| POST | `/api/auth/student/signup` | Frontend | Creates a student account |
| POST | `/api/auth/student/login` | Frontend | Logs a student in |
| POST | `/api/auth/company/login` | Frontend | Logs a company in |
| POST | `/api/admin/company/provision` | You (admin) | Creates a company account — protected by `X-Admin-Secret` header |
| POST | `/api/student/assessment/submit` | `technical-assessment.html` | Saves a finished attempt (score, pass/fail, violations) and updates `assessment_status` |
| GET | `/api/student/assessment/attempts` | Frontend (optional) | Returns the logged-in student's own attempt history |
| GET | `/api/health` | Anyone | Health check |

## 6. Technical assessment flow

`technical-assessment.html` is the page students land on from the **"Take
Assessment Now"** button on the dashboard. It requires a session (redirects
to `login.html` if there's no `grove_token`), and for each of the 5
technical domains runs:

1. **Round 1 — MCQ** (needs `MCQ_PASS_PCT`, default 60%, to advance)
2. **Round 2 — Coding challenge** (all test cases must pass)

Both rounds are proctored by a client-side anti-cheat engine (fullscreen
lock, tab-switch/blur detection, disabled copy-paste, a strike counter that
auto-fails at `MAX_VIOLATIONS`). Whatever the outcome, the result is POSTed
to `/api/student/assessment/submit`, which:

- Logs the attempt in `assessment_attempts`.
- On a **pass**: sets `profiles.assessment_status = 'eligible'` and
  `profiles.qualified_domain`, then the page redirects to
  `dashboard.html`.
- On a **fail or anti-cheat flag**: sets `profiles.assessment_status =
  'failed'`, and the page sends the student to `LMS_LINK` (edit this
  constant near the top of `technical-assessment.html`'s `<script>` to your
  real LMS URL) instead of the dashboard.

The dashboard's CTA bar reads `assessment_status` back and changes its label
(`Take Assessment Now` → `Retake Assessment` → `You're Eligible ✓`).

Questions and coding problems currently live in the `DOMAINS` array inside
`technical-assessment.html` (not the database) — edit that array to change
or add questions per domain.

### Provisioning a company account

Since companies don't self-signup, you create their login yourself:

```bash
curl -X POST http://localhost:8000/api/admin/company/provision \
  -H "Content-Type: application/json" \
  -H "X-Admin-Secret: your-secret-from-.env" \
  -d '{
    "company_name": "Acme Corp",
    "email": "hr@acme.com",
    "password": "TempPassword123"
  }'
```

Then send those credentials to the company so they can log in on the
Company tab.

## 5. Notes

- Passwords are never handled or stored by this backend directly — Supabase
  Auth hashes and stores them for you.
- The `service_role` key bypasses Row Level Security, so it's only ever used
  server-side (in `app.py`), never shipped to the browser.
- For production: restrict CORS `allow_origins` in `app.py` to your real
  frontend domain, and turn "Confirm email" back on in Supabase.
