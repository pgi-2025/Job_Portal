"""
Plant Green Inertia — Job Portal API (Flask edition)
---------------------------------------
Flask backend that uses Supabase for authentication and storage.

Two kinds of accounts share Supabase's built-in auth.users table, and are
told apart by a "role" column on a public.profiles table:

  - student : self-service signup (see /api/auth/student/signup)
  - company : provisioned only by an admin (see /api/admin/company/provision)

Company accounts are additionally linked to a row in `companies` (the
tie-up-company directory shown on the student dashboard) via the
`company_login` table — see provision_company() below.

Run locally:
    pip install -r requirements.txt
    cp .env.example .env      # then fill in your Supabase keys
    python app.py             # runs on http://127.0.0.1:8000
"""

import os
import hmac
import hashlib
import requests
from datetime import datetime, timezone
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from supabase import create_client, Client
from flask import send_file
from flask import send_from_directory


load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ADMIN_PROVISION_SECRET = os.environ.get("ADMIN_PROVISION_SECRET", "")

# ---- WhatsApp channel broadcast (new openings -> your students' channel) ----
# Works with any WhatsApp automation provider that accepts a simple
# {"to": <channel id>, "body": <text>} POST (e.g. Whapi.Cloud). Meta's
# official Cloud API does not yet support posting to Channels directly.
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RETEST_FEE_PAISE = 4900  # Rs 1  (900 = Rs 9)
WHATSAPP_API_URL = os.environ.get("WHATSAPP_API_URL", "")
WHATSAPP_API_TOKEN = os.environ.get("WHATSAPP_API_TOKEN", "")
WHATSAPP_CHANNEL_ID = os.environ.get("WHATSAPP_CHANNEL_ID", "")

if not (SUPABASE_URL and SUPABASE_ANON_KEY and SUPABASE_SERVICE_ROLE_KEY):
    raise RuntimeError(
        "Missing Supabase config. Copy .env.example to .env and fill in "
        "SUPABASE_URL, SUPABASE_ANON_KEY and SUPABASE_SERVICE_ROLE_KEY."
    )

# Anon client: kept for stateless/read-only use. Never used for sign-in or
# sign-up directly — see _fresh_client() below for why.
supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# Admin client: service-role key, bypasses Row Level Security. Only ever
# used server-side, never exposed to the frontend.
supabase_admin: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# app = Flask(__name__)
# CORS(app, resources={r"/api/*": {"origins": "*"}})  # tighten to your real frontend origin(s) in production

app = Flask(__name__)

CORS(
    app,
    resources={
        r"/api/*": {
            "origins": [
                "https://job-portal-uar6.onrender.com",
                "http://localhost:8000",
                "http://127.0.0.1:8000"
            ]
        }
    },
    supports_credentials=True
)



@app.after_request
def no_cache_html(resp):
    # Always serve the latest HTML/JS so updated pages show without a hard refresh.
    if request.path.endswith((".html", ".js")) or request.path == "/":
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


def _fresh_client() -> Client:
    """
    A throwaway, session-less Supabase client for one-off auth operations
    (sign in / sign up).

    Why not just reuse the module-level `supabase` client? Flask's dev
    server (and most WSGI servers) can handle requests concurrently, and
    that client is a shared singleton. supabase-py's sign_in_with_password()
    / sign_up() calls store the resulting session *on the client instance
    itself* — so two people logging in around the same moment could
    clobber each other's in-memory session on a shared client. None of our
    routes rely on that stored session anyway (we hand the JWT back to the
    frontend and re-validate it per request in login_required), so every
    auth call gets its own fresh client instead.
    """
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

@app.route("/")
def serve_login():
    return send_from_directory(BASE_DIR, "login.html")


@app.route("/<path:filename>")
def serve_static_page(filename):
    return send_from_directory(BASE_DIR, filename)


# ============================== ERROR HELPERS ==============================

def error(message: str, status: int = 400):
    return jsonify({"detail": message}), status


def require_fields(data: dict, fields: list):
    """Returns a list of missing/empty required field names."""
    return [f for f in fields if not data.get(f)]


def notify_whatsapp_channel(text: str):
    """
    Posts a text message to your students' WhatsApp channel. Configure
    WHATSAPP_API_URL / WHATSAPP_API_TOKEN / WHATSAPP_CHANNEL_ID in .env —
    if any are missing this quietly does nothing, and any send failure is
    only logged (never breaks the caller, e.g. saving openings).
    """
    if not (WHATSAPP_API_URL and WHATSAPP_API_TOKEN and WHATSAPP_CHANNEL_ID):
        return
    try:
        requests.post(
            WHATSAPP_API_URL,
            headers={
                "Authorization": f"Bearer {WHATSAPP_API_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"to": WHATSAPP_CHANNEL_ID, "body": text},
            timeout=8,
        )
    except Exception as e:
        print("WhatsApp notify failed:", repr(e))


# ============================== HELPERS ==============================

def _get_profile(user_id: str):
    result = (
        supabase_admin.table("profiles")
        .select("*")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    return result.data if result else None


def login_required(f):
    """Reads the Supabase session token from the Authorization header,
    validates it, and stashes the user on flask.g.user for routes the
    dashboard calls after login."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            return error("Missing or malformed auth token.", 401)
        token = authorization.split(" ", 1)[1]
        try:
            # get_user(token) validates the given JWT directly — it doesn't
            # depend on (or mutate) any session stored on the client, so
            # it's safe to call on the shared `supabase` client.
            user_resp = supabase.auth.get_user(token)
        except Exception as e:
            # Log the reason (never the token). Only genuine auth failures are a 401;
            # a Supabase outage/network error must not look like an expired login.
            print("login_required:", type(e).__name__, str(e)[:160])
            if type(e).__name__.startswith("Auth"):
                return error("Invalid or expired session.", 401)
            return error("Could not verify your session right now. Please try again.", 503)
        user = user_resp.user if user_resp else None
        if not user:
            return error("Invalid or expired session.", 401)
        g.user = user
        return f(*args, **kwargs)
    return wrapper


def _require_student():
    profile = _get_profile(g.user.id)
    if not profile or profile.get("role") != "student":
        return None
    return profile


def _require_company():
    profile = _get_profile(g.user.id)
    if not profile or profile.get("role") != "company":
        return None
    return profile


def _get_company_id_for_user(user_id: str):
    print("Logged in User ID:", user_id)

    result = (
        supabase_admin.table("company_login")
        .select("*")
        .eq("profile_id", user_id)
        .eq("is_active", True)
        .execute()
    )

    print("Company Login Result:", result.data)

    rows = result.data or []
    return rows[0]["company_id"] if rows else None


def _login(payload: dict, expected_role: str):
    missing = require_fields(payload, ["email", "password"])
    if missing:
        return error(f"Missing field(s): {', '.join(missing)}.", 422)

    client = _fresh_client()
    try:
        result = client.auth.sign_in_with_password(
            {"email": payload["email"], "password": payload["password"]}
        )
    except Exception:
        return error("Invalid email or password.", 401)

    user = result.user
    session = result.session
    if not user or not session:
        return error("Invalid email or password.", 401)

    profile = _get_profile(user.id)
    role = profile.get("role") if profile else None

    if role != expected_role:
        return error(f"No {expected_role} account found for this email.", 403)

    if role == "student":
        supabase_admin.table("profiles").update(
            {"last_login": datetime.now(timezone.utc).isoformat()}
        ).eq("id", user.id).execute()

    return jsonify({
        "access_token": session.access_token,
        "refresh_token": session.refresh_token,
        "role": role,
        "profile": {**(profile or {}), "email": user.email},
    })


# ============================== ROUTES ==============================

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/api/auth/student/signup", methods=["POST"])
def student_signup():
    """Students self-register. Creates a Supabase auth user + profile row."""
    payload = request.get_json(silent=True) or {}
    missing = require_fields(payload, ["full_name", "college", "email", "password"])
    if missing:
        return error(f"Missing field(s): {', '.join(missing)}.", 422)
    if len(payload["password"]) < 6:
        return error("Password must be at least 6 characters.", 422)

    client = _fresh_client()
    try:
        result = client.auth.sign_up(
            {"email": payload["email"], "password": payload["password"]}
        )
    except Exception as e:
        return error(str(e), 400)

    user = result.user
    if not user:
        return error("Sign up failed. Please try again.", 400)

    supabase_admin.table("profiles").insert(
        {
            "id": user.id,
            "role": "student",
            "full_name": payload["full_name"],
            "college": payload["college"],
        }
    ).execute()

    return jsonify({"message": "Account created.", "user_id": user.id})


@app.route("/api/auth/student/login", methods=["POST"])
def student_login():
    payload = request.get_json(silent=True) or {}
    return _login(payload, expected_role="student")


@app.route("/api/auth/company/login", methods=["POST"])
def company_login_route():
    payload = request.get_json(silent=True) or {}
    return _login(payload, expected_role="company")


def _provision_one_company(payload: dict):
    """
    Does the actual work of provisioning a single company login. Returns
    (result_dict, status_code). Shared by the single-company route and the
    bulk route below, so both stay in sync.
    """
    missing = require_fields(payload, ["company_name", "email", "password"])
    if missing:
        return {"error": f"Missing field(s): {', '.join(missing)}."}, 422
    if len(payload["password"]) < 6:
        return {"error": "Password must be at least 6 characters."}, 422

    try:
        created = supabase_admin.auth.admin.create_user(
            {
                "email": payload["email"],
                "password": payload["password"],
                "email_confirm": True,
            }
        )
    except Exception as e:
        return {"error": str(e)}, 400

    user = created.user
    if not user:
        return {"error": "Could not create company account."}, 400

    supabase_admin.table("profiles").insert(
        {
            "id": user.id,
            "role": "company",
            "company_name": payload["company_name"],
        }
    ).execute()

    # ---- find-or-create the tie-up company directory row ----
    try:
        existing = (
            supabase_admin.table("companies")
            .select("id")
            .eq("name", payload["company_name"])
            .maybe_single()
            .execute()
        )
    except Exception:
        existing = None

    if existing and existing.data:
        company_id = existing.data["id"]
    else:
        inserted = (
            supabase_admin.table("companies")
            .insert(
                {
                    "name": payload["company_name"],
                    "roles": payload.get("roles", []),
                    "sort_order": payload.get("sort_order", 0),
                }
            )
            .execute()
        )
        company_id = inserted.data[0]["id"]

    # ---- link the login to that directory row ----
    try:
        supabase_admin.table("company_login").insert(
            {
                "company_id": company_id,
                "profile_id": user.id,
                "login_email": payload["email"],
            }
        ).execute()
    except Exception as e:
        return {"error": f"Company account created, but linking failed: {e}"}, 500

    return {
        "message": "Company account created.",
        "user_id": user.id,
        "company_id": company_id,
        "company_name": payload["company_name"],
        "email": payload["email"],
    }, 200


@app.route("/api/admin/company/provision", methods=["POST"])
def provision_company():
    """
    Companies don't self-signup — Plant Green Inertia staff provision their
    login here. Protected by a shared secret header (X-Admin-Secret), not
    for frontend use.

    Example:
        curl -X POST http://localhost:8000/api/admin/company/provision \\
          -H "Content-Type: application/json" \\
          -H "X-Admin-Secret: your-secret" \\
          -d '{"company_name":"Acme Corp","email":"hr@acme.com","password":"changeme123","roles":["SDE","QA"]}'
    """
    x_admin_secret = request.headers.get("X-Admin-Secret")
    if not ADMIN_PROVISION_SECRET or x_admin_secret != ADMIN_PROVISION_SECRET:
        return error("Unauthorized.", 401)

    payload = request.get_json(silent=True) or {}
    result, status = _provision_one_company(payload)
    if status != 200:
        return error(result["error"], status)
    return jsonify(result)


@app.route("/api/admin/company/provision-bulk", methods=["POST"])
def provision_company_bulk():
    """
    Provisions logins for many companies in one call. Body:
        { "companies": [
            {"company_name":"...", "email":"...", "password":"...", "roles":[...]},
            ...
        ] }

    Returns a per-company result list so you can see exactly which ones
    succeeded or failed (and why) without re-running the whole batch.

    Example:
        curl -X POST http://localhost:8000/api/admin/company/provision-bulk \\
          -H "Content-Type: application/json" \\
          -H "X-Admin-Secret: your-secret" \\
          -d '{"companies":[{"company_name":"Acme Corp","email":"hr@acme.com","password":"changeme123"}]}'
    """
    x_admin_secret = request.headers.get("X-Admin-Secret")
    if not ADMIN_PROVISION_SECRET or x_admin_secret != ADMIN_PROVISION_SECRET:
        return error("Unauthorized.", 401)

    payload = request.get_json(silent=True) or {}
    companies = payload.get("companies")
    if not isinstance(companies, list) or not companies:
        return error("Body must include a non-empty 'companies' list.", 422)

    results = []
    for entry in companies:
        result, status = _provision_one_company(entry)
        results.append({
            "company_name": entry.get("company_name"),
            "email": entry.get("email"),
            "success": status == 200,
            "detail": result.get("error") if status != 200 else "Created.",
        })

    return jsonify({"results": results})


# ============================== STUDENT DASHBOARD ==============================

@app.route("/api/student/me", methods=["GET"])
@login_required
def get_me():
    """Returns the logged-in student's saved profile."""
    profile = _require_student()
    if profile is None:
        return error("Student profile not found.", 403)
    return jsonify({"profile": {**profile, "email": g.user.email}})


@app.route("/api/student/profile", methods=["PUT"])
@login_required
def update_profile():
    """Saves profile edits (name, mobile, college, degree, age) to the database."""
    if _require_student() is None:
        return error("Student profile not found.", 403)

    payload = request.get_json(silent=True) or {}
    allowed_fields = ["full_name", "mobile_number", "college", "degree", "age",
                       "resume_summary", "resume_skills", "resume_experience", "resume_projects",
                       "employment_type", "experience_years", "salary_expectation"]
    updates = {k: v for k, v in payload.items() if k in allowed_fields and v is not None}

    if "age" in updates:
        try:
            updates["age"] = int(updates["age"])
        except (TypeError, ValueError):
            return error("Age must be a number.", 422)

    updates["updated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        supabase_admin.table("profiles").update(updates).eq("id", g.user.id).execute()
    except Exception as e:
        return error(f"Could not save profile: {e}", 500)
    updated = _get_profile(g.user.id)
    return jsonify({"message": "Profile updated.", "profile": updated})


@app.route("/api/student/upload-resume", methods=["POST"])
@login_required
def upload_resume():
    """Uploads a resume PDF to Supabase Storage and saves its URL on the profile."""
    if _require_student() is None:
        return error("Student profile not found.", 403)

    file = request.files.get("file")
    if not file or not file.filename:
        return error("No file uploaded.", 422)

    contents = file.read()
    ext = file.filename.rsplit(".", 1)[-1] if "." in file.filename else "pdf"
    path = f"{g.user.id}/resume.{ext}"

    try:
        supabase_admin.storage.from_("resumes").upload(
            path,
            contents,
            {"content-type": file.content_type or "application/pdf", "upsert": "true"},
        )
    except Exception as e:
        return error(f"Resume upload failed: {e}", 400)

    public_url = supabase_admin.storage.from_("resumes").get_public_url(path)
    supabase_admin.table("profiles").update(
        {"resume_url": public_url, "updated_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", g.user.id).execute()

    return jsonify({"message": "Resume uploaded.", "resume_url": public_url})


@app.route("/api/student/upload-photo", methods=["POST"])
@login_required
def upload_photo():
    """Uploads a profile photo to Supabase Storage and saves its URL on the profile."""
    if _require_student() is None:
        return error("Student profile not found.", 403)

    file = request.files.get("file")
    if not file or not file.filename:
        return error("No file uploaded.", 422)

    contents = file.read()
    ext = file.filename.rsplit(".", 1)[-1] if "." in file.filename else "jpg"
    path = f"{g.user.id}/photo.{ext}"

    try:
        supabase_admin.storage.from_("photos").upload(
            path,
            contents,
            {"content-type": file.content_type or "image/jpeg", "upsert": "true"},
        )
    except Exception as e:
        return error(f"Photo upload failed: {e}", 400)

    public_url = supabase_admin.storage.from_("photos").get_public_url(path)
    supabase_admin.table("profiles").update(
        {"photo_url": public_url, "updated_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", g.user.id).execute()

    return jsonify({"message": "Photo uploaded.", "photo_url": public_url})


@app.route("/api/company/request-access", methods=["POST"])
def request_company_access():
    """
    Public endpoint for the "request access" link on the company login page.
    Anyone can submit this (no login yet) — it just stores the request so
    PGI staff can review it and provision a real company account.
    """
    payload = request.get_json(silent=True) or {}
    fields = ["company_name", "email", "website", "contact_number", "location"]
    missing = require_fields(payload, fields)
    if missing:
        return error(f"Missing required field(s): {', '.join(missing)}", 422)

    row = {k: str(payload[k]).strip() for k in fields}
    try:
        supabase_admin.table("company_access_requests").insert(row).execute()
    except Exception as e:
        return error(f"Could not submit request: {e}", 500)
    return jsonify({"message": "Request submitted."})


@app.route("/api/dashboard/companies", methods=["GET"])
def list_companies():
    """
    Public list of tie-up companies shown on the student dashboard.

    dashboard.html renders each result as `c.name` on the card and
    `c.roles` (an array of strings) in the click-through modal — this is
    the same `companies` table that company_login links a login to.
    """
    result = supabase_admin.table("companies").select("*").order("sort_order").execute()
    return jsonify({"companies": result.data})


@app.route("/api/dashboard/drives", methods=["GET"])
def list_drives():
    """
    Public list of upcoming placement drives shown on the student dashboard.

    dashboard.html expects `date_label` and `title` on each row —
    make sure your `drives` table has those columns.
    """
    result = supabase_admin.table("drives").select("*").order("sort_order").execute()
    return jsonify({"drives": result.data})


# ============================== TECHNICAL ASSESSMENT ==============================

@app.route("/api/student/assessment/submit", methods=["POST"])
@login_required
def submit_assessment():
    """
    Called once by technical-assessment.html when a student finishes an
    attempt for a domain (pass, fail, or anti-cheat flag).

    - Logs the attempt in assessment_attempts (score, round results,
      violation count) so there's a full history.
    - On a pass, marks the student's profile assessment_status = 'eligible'
      and records which domain they qualified in — this is what "unlocks"
      the real student dashboard for them, and what makes them show up in
      /api/company/candidates below. On a fail/flag, status becomes
      'failed' and the frontend sends the student to the LMS instead.
    """
    if _require_student() is None:
        return error("Student profile not found.", 403)

    payload = request.get_json(silent=True) or {}
    missing = require_fields(payload, ["domain"])
    if missing:
        return error(f"Missing field(s): {', '.join(missing)}.", 422)
    if "overall_passed" not in payload:
        return error("Missing field(s): overall_passed.", 422)

    overall_passed = bool(payload["overall_passed"])

    supabase_admin.table("assessment_attempts").insert(
        {
            "student_id": g.user.id,
            "domain": payload["domain"],
            "round1_score": payload.get("round1_score"),
            "round1_correct": payload.get("round1_correct"),
            "round1_total": payload.get("round1_total"),
            "round1_breakdown": payload.get("round1_breakdown"),
            "round1_passed": payload.get("round1_passed"),
            "round2_passed": payload.get("round2_passed"),
            "round2_correct": payload.get("round2_correct"),
            "round2_total": payload.get("round2_total"),
            "overall_passed": overall_passed,
            "violations": payload.get("violations", 0),
            "flagged": payload.get("flagged", False),
        }
    ).execute()

    # A fail in a NEW domain must never erase eligibility earned by
    # passing an EARLIER domain — assessment_status is a one-way
    # upgrade (failed/None -> eligible), never a downgrade. Without
    # this, a student who passed "hr" and later failed "marketing"
    # would silently vanish from every company's candidate list, even
    # though their original pass is still valid.
    current_profile = _get_profile(g.user.id) or {}
    already_eligible = current_profile.get("assessment_status") == "eligible"

    if overall_passed:
        status = "eligible"
    elif already_eligible:
        status = "eligible"  # keep the earlier pass; this new fail doesn't count against it
    else:
        status = "failed"

    updates = {
        "assessment_status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    # Only move qualified_domain forward on an actual pass — a fail in
    # a different domain must not overwrite the domain they already
    # qualified in.
    if overall_passed:
        updates["qualified_domain"] = payload["domain"]
        updates["round1_correct"] = payload.get("round1_correct")
        updates["round1_total"] = payload.get("round1_total")
        updates["round1_breakdown"] = payload.get("round1_breakdown")
        updates["round2_correct"] = payload.get("round2_correct")
        updates["round2_total"] = payload.get("round2_total")

    supabase_admin.table("profiles").update(updates).eq("id", g.user.id).execute()

    return jsonify({"message": "Assessment result saved.", "status": updates["assessment_status"]})


# ---- Retest payment: 1st test free, every later test costs Rs 9 ----

def _has_paid_retest(student_id):
    """Returns an unused paid retest row, or None. Any 'created' order is also checked
    directly with Razorpay, so a payment is never lost if the browser closed or the
    network dropped before verify-payment ran."""
    def rows(st):
        return (supabase_admin.table("assessment_payments").select("id, razorpay_order_id")
                .eq("student_id", student_id).eq("status", st)
                .order("created_at", desc=True).limit(5).execute().data or [])
    paid = rows("paid")
    if paid:
        return paid[0]
    if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
        for row in rows("created"):
            try:
                r = requests.get(f"https://api.razorpay.com/v1/orders/{row['razorpay_order_id']}/payments",
                                 auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET), timeout=15)
                done = next((p for p in (r.json().get("items", []) if r.status_code == 200 else [])
                             if p.get("status") == "captured"), None)
                if done:
                    supabase_admin.table("assessment_payments").update(
                        {"status": "paid", "razorpay_payment_id": done.get("id")}
                    ).eq("id", row["id"]).execute()
                    return row
            except Exception as e:
                print("retest payment check failed:", repr(e))
    return None


@app.route("/api/student/assessment/access", methods=["GET"])
@login_required
def assessment_access():
    """Tells the frontend whether this test is free, needs payment, or is already paid."""
    profile = _require_student()
    if profile is None:
        return error("Student profile not found.", 403)
    if not profile.get("free_test_used"):
        return jsonify({"allowed": True, "free": True, "fee": RETEST_FEE_PAISE // 4900})
    return jsonify({"allowed": bool(_has_paid_retest(g.user.id)), "free": False, "fee": RETEST_FEE_PAISE // 100,
                    "razorpay_key": RAZORPAY_KEY_ID})


@app.route("/api/student/assessment/create-order", methods=["POST"])
@login_required
def create_retest_order():
    if _require_student() is None:
        return error("Student profile not found.", 403)
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        return error("Payments are not configured.", 500)
    try:
        r = requests.post("https://api.razorpay.com/v1/orders",
                          auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
                          json={"amount": RETEST_FEE_PAISE, "currency": "INR",
                                "receipt": f"retest_{g.user.id}"[:40]}, timeout=20)
    except requests.RequestException as e:
        print("create-order: could not reach Razorpay:", repr(e))
        return error("Could not reach the payment gateway.", 502)
    if r.status_code != 200:
        print(f"create-order: Razorpay refused ({r.status_code}, key mode {RAZORPAY_KEY_ID[:9]}):", r.text[:300])
        return error("Could not create payment order.", 502)
    order = r.json()
    try:
        supabase_admin.table("assessment_payments").insert(
            {"student_id": g.user.id, "razorpay_order_id": order["id"],
             "amount_paise": RETEST_FEE_PAISE}).execute()
    except Exception as e:
        print("create-order: Supabase insert failed:", repr(e))
        return error("Could not record the payment order.", 500)
    print(f"create-order: {order['id']} for student {g.user.id}, {RETEST_FEE_PAISE} paise")
    return jsonify({"order_id": order["id"], "amount": RETEST_FEE_PAISE,
                    "key": RAZORPAY_KEY_ID})


@app.route("/api/student/assessment/verify-payment", methods=["POST"])
@login_required
def verify_retest_payment():
    if _require_student() is None:
        return error("Student profile not found.", 403)
    p = request.get_json(silent=True) or {}
    order_id, pay_id, sig = p.get("razorpay_order_id"), p.get("razorpay_payment_id"), p.get("razorpay_signature")
    if not (order_id and pay_id and sig):
        return error("Missing payment details.", 422)
    if not RAZORPAY_KEY_SECRET:
        print("verify-payment: RAZORPAY_KEY_SECRET is not set on the server")
        return error("Payments are not configured.", 500)
    expected = hmac.new(RAZORPAY_KEY_SECRET.encode(), f"{order_id}|{pay_id}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(sig)):
        print(f"verify-payment: bad signature for order {order_id}, student {g.user.id}")
        return error("Payment verification failed.", 400)
    # Signature is valid -> only now is the payment marked paid.
    try:
        res = supabase_admin.table("assessment_payments").update(
            {"status": "paid", "razorpay_payment_id": pay_id}
        ).eq("razorpay_order_id", order_id).eq("student_id", g.user.id).eq("status", "created").execute()
        if not res.data:  # already paid/used (verified twice), or order not found
            known = (supabase_admin.table("assessment_payments").select("id")
                     .eq("razorpay_order_id", order_id).eq("student_id", g.user.id).execute().data)
            if not known:
                print(f"verify-payment: no order {order_id} for student {g.user.id}")
                return error("Payment order not found.", 404)
    except Exception as e:
        print("verify-payment: Supabase error:", repr(e))
        return error("Could not save the payment. Please try again.", 500)
    print(f"verify-payment: order {order_id} / payment {pay_id} marked paid for student {g.user.id}")
    return jsonify({"message": "Payment verified."})


@app.route("/api/student/assessment/start", methods=["POST"])
@login_required
def start_assessment():
    """Called when Round 1 actually begins. Uses up the free test, or one paid retest credit."""
    profile = _require_student()
    if profile is None:
        return error("Student profile not found.", 403)
    if not profile.get("free_test_used"):
        supabase_admin.table("profiles").update({"free_test_used": True}).eq("id", g.user.id).execute()
        return jsonify({"message": "Free test started."})
    paid = _has_paid_retest(g.user.id)
    if not paid:
        return error(f"Retest fee of Rs {RETEST_FEE_PAISE // 100} required.", 402)
    supabase_admin.table("assessment_payments").update({"status": "consumed"}).eq("id", paid["id"]).execute()
    return jsonify({"message": "Paid retest started."})


@app.route("/api/student/assessment/attempts", methods=["GET"])
@login_required
def list_my_attempts():
    """Returns this student's own assessment attempt history, most recent first."""
    if _require_student() is None:
        return error("Student profile not found.", 403)

    result = (
        supabase_admin.table("assessment_attempts")
        .select("*")
        .eq("student_id", g.user.id)
        .order("created_at", desc=True)
        .execute()
    )
    return jsonify({"attempts": result.data})


@app.route("/api/student/assessment/violation", methods=["POST"])
@login_required
def log_assessment_violation():
    """
    Called by violationManager.js (proctoring system) every time a
    proctoring violation fires — mobile phone, no face, multiple faces,
    or looking away. Fire-and-forget from the frontend's perspective;
    logs into assessment_violations for later review.

    This is separate from /api/student/assessment/submit, which logs
    the final pass/fail result of a whole round — this endpoint logs
    each individual violation event as it happens, in real time.
    """
    if _require_student() is None:
        return error("Student profile not found.", 403)

    payload = request.get_json(silent=True) or {}
    missing = require_fields(payload, ["violation_type"])
    if missing:
        return error(f"Missing field(s): {', '.join(missing)}.", 422)

    valid_types = {"phone", "no_face", "looking_away", "multiple_faces"}
    if payload["violation_type"] not in valid_types:
        return error(
            f"Invalid violation_type. Must be one of: {', '.join(sorted(valid_types))}.",
            422,
        )

    confidence = payload.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            return error("confidence must be a number.", 422)

    supabase_admin.table("assessment_violations").insert(
        {
            "student_id": g.user.id,
            "assessment_id": payload.get("assessment_id"),
            "violation_type": payload["violation_type"],
            "confidence": confidence,
            "detected_at": payload.get("detected_at") or datetime.now(timezone.utc).isoformat(),
        }
    ).execute()

    return jsonify({"message": "Violation logged."})


# ============================== COMPANY PORTAL ==============================

@app.route("/api/company/me", methods=["GET"])
@login_required
def company_me():
    """Returns the logged-in company's saved profile (used to paint the navbar/hero)."""
    profile = _require_company()
    if profile is None:
        return error("Company profile not found.", 403)
    return jsonify({"profile": profile})


@app.route("/api/company/openings", methods=["GET"])
@login_required
def get_company_openings():
    """Returns the logged-in company's directory row (name, roles, has_openings)."""
    if _require_company() is None:
        return error("Company profile not found.", 403)
    company_id = _get_company_id_for_user(g.user.id)
    if not company_id:
        return error("No linked company record found.", 404)
    result = (
        supabase_admin.table("companies")
        .select("id, name, roles, has_openings, application_fields")
        .eq("id", company_id)
        .maybe_single()
        .execute()
    )
    return jsonify({"company": result.data})


@app.route("/api/company/openings", methods=["PUT"])
@login_required
def update_company_openings():
    """
    Lets a company set its own open roles and open/closed status, shown to
    students on the dashboard as role chips + a green/red indicator dot.
    Body: { "roles": ["Role A", "Role B"], "has_openings": true }
    """
    if _require_company() is None:
        return error("Company profile not found.", 403)
    company_id = _get_company_id_for_user(g.user.id)
    if not company_id:
        return error("No linked company record found.", 404)

    payload = request.get_json(silent=True) or {}
    updates = {}
    if "roles" in payload:
        roles = payload["roles"]
        if not isinstance(roles, list):
            return error("roles must be a list of strings.", 422)
        updates["roles"] = [str(r).strip() for r in roles if str(r).strip()]
    if "has_openings" in payload:
        updates["has_openings"] = bool(payload["has_openings"])
    if "application_fields" in payload:
        fields = payload["application_fields"]
        if not isinstance(fields, list):
            return error("application_fields must be a list of strings.", 422)
        updates["application_fields"] = [str(f).strip() for f in fields if str(f).strip()]

    if not updates:
        return error("Nothing to update.", 422)

    try:
        supabase_admin.table("companies").update(updates).eq("id", company_id).execute()
    except Exception as e:
        print("update_company_openings error:", repr(e))
        return error(f"Could not save openings: {e}", 500)

    result = (
        supabase_admin.table("companies")
        .select("id, name, roles, has_openings, application_fields")
        .eq("id", company_id)
        .maybe_single()
        .execute()
    )
    company = result.data or {}

    # New/updated roles while openings are live -> broadcast to the WhatsApp channel.
    if "roles" in updates and company.get("has_openings") and company.get("roles"):
        roles_text = ", ".join(company["roles"])
        notify_whatsapp_channel(
            f"🚀 New openings at *{company.get('name', 'A company')}*!\n"
            f"Roles: {roles_text}\n"
            f"Log in to the PGI Job Portal to apply."
        )

    return jsonify({"message": "Openings updated.", "company": company})


@app.route("/api/company/logo", methods=["POST"])
@login_required
def upload_company_logo():
    """Uploads a company logo to Supabase Storage and saves its URL on the tie-up row."""
    if _require_company() is None:
        return error("Company profile not found.", 403)
    company_id = _get_company_id_for_user(g.user.id)
    if not company_id:
        return error("No linked company record found.", 404)

    file = request.files.get("file")
    if not file or not file.filename:
        return error("No file uploaded.", 422)

    contents = file.read()
    ext = file.filename.rsplit(".", 1)[-1] if "." in file.filename else "png"
    path = f"{company_id}/logo.{ext}"

    try:
        supabase_admin.storage.from_("logos").upload(
            path, contents,
            {"content-type": file.content_type or "image/png", "upsert": "true"},
        )
    except Exception as e:
        return error(f"Logo upload failed: {e}", 400)

    public_url = supabase_admin.storage.from_("logos").get_public_url(path)
    supabase_admin.table("companies").update({"logo_url": public_url}).eq("id", company_id).execute()
    return jsonify({"message": "Logo uploaded.", "logo_url": public_url})


# ============================== CHAT (company <-> student) ==============================
# Keeps interview scheduling / Google Meet links inside the platform instead of
# exposing the student's phone number to companies.

def _chat_identity():
    """Returns ('company', company_id) or ('student', student_id) for the caller, or None."""
    profile = _get_profile(g.user.id)
    if not profile:
        return None
    if profile.get("role") == "company":
        return ("company", _get_company_id_for_user(g.user.id))
    if profile.get("role") == "student":
        return ("student", g.user.id)
    return None


@app.route("/api/chat/conversations", methods=["GET"])
@login_required
def get_chat_conversations():
    """Student-only: list companies that have messaged this student."""
    identity = _chat_identity()
    if not identity or identity[0] != "student" or not identity[1]:
        return error("Not authorized.", 403)
    student_id = identity[1]

    msgs = (
        supabase_admin.table("messages")
        .select("company_id, sender_role, body, created_at")
        .eq("student_id", student_id)
        .order("created_at")
        .execute()
    ).data or []

    by_company = {}
    for m in msgs:
        by_company[m["company_id"]] = m  # keep latest (rows are ordered ascending)
    company_ids = [cid for cid, m in by_company.items() if any(x["sender_role"] == "company" for x in msgs if x["company_id"] == cid)]
    if not company_ids:
        return jsonify({"conversations": []})

    companies = (
        supabase_admin.table("companies")
        .select("id, name")
        .in_("id", company_ids)
        .execute()
    ).data or []
    comp_map = {c["id"]: c for c in companies}

    conversations = [
        {
            "company_id": cid,
            "company_name": comp_map.get(cid, {}).get("name", "Company"),
            "logo_url": None,
            "last_message": by_company[cid]["body"],
            "last_message_at": by_company[cid]["created_at"],
        }
        for cid in company_ids
    ]
    conversations.sort(key=lambda c: c["last_message_at"], reverse=True)
    return jsonify({"conversations": conversations})


@app.route("/api/chat/messages", methods=["GET"])
@login_required
def get_chat_messages():
    identity = _chat_identity()
    if not identity or not identity[1]:
        return error("Not authorized.", 403)
    role, my_id = identity
    company_id = request.args.get("company_id")
    student_id = request.args.get("student_id")
    if not company_id or not student_id:
        return error("company_id and student_id are required.", 422)
    if (role == "company" and company_id != my_id) or (role == "student" and student_id != my_id):
        return error("Not authorized.", 403)

    result = (
        supabase_admin.table("messages")
        .select("*")
        .eq("company_id", company_id).eq("student_id", student_id)
        .order("created_at")
        .execute()
    )
    return jsonify({"messages": result.data})


@app.route("/api/chat/messages", methods=["POST"])
@login_required
def send_chat_message():
    identity = _chat_identity()
    if not identity or not identity[1]:
        return error("Not authorized.", 403)
    role, my_id = identity

    payload = request.get_json(silent=True) or {}
    company_id = payload.get("company_id")
    student_id = payload.get("student_id")
    body = (payload.get("message") or "").strip()
    if not company_id or not student_id or not body:
        return error("company_id, student_id and message are required.", 422)
    if (role == "company" and company_id != my_id) or (role == "student" and student_id != my_id):
        return error("Not authorized.", 403)

    if role == "student":
        existing = (
            supabase_admin.table("messages")
            .select("id")
            .eq("company_id", company_id).eq("student_id", student_id)
            .eq("sender_role", "company")
            .limit(1)
            .execute()
        )
        if not existing.data:
            return error("The company hasn't started this conversation yet.", 403)

    inserted = (
        supabase_admin.table("messages")
        .insert({
            "company_id": company_id,
            "student_id": student_id,
            "sender_role": role,
            "body": body,
        })
        .execute()
    )
    return jsonify({"message_row": inserted.data[0] if inserted.data else None})


@app.route("/api/company/select-candidate", methods=["POST"])
@login_required
def company_select_candidate():
    """Company marks a candidate as selected/hired. Body: {"student_id": "..."}.
    Waits on the student's own confirmation before the placement is final."""
    if _require_company() is None:
        return error("Company profile not found.", 403)
    company_id = _get_company_id_for_user(g.user.id)
    if not company_id:
        return error("No active company login.", 403)

    payload = request.get_json(silent=True) or {}
    student_id = payload.get("student_id")
    if not student_id:
        return error("student_id is required.", 400)

    student = _get_profile(student_id)
    if not student or student.get("is_placed"):
        return error("Candidate unavailable.", 400)

    updates = {"selected_by_company_id": company_id, "company_confirmed_hire": True}
    if student.get("student_confirmed_hire"):
        updates["is_placed"] = True
    supabase_admin.table("profiles").update(updates).eq("id", student_id).execute()
    return jsonify({"message": "Candidate marked as selected."})


@app.route("/api/student/confirm-placement", methods=["POST"])
@login_required
def student_confirm_placement():
    """Student confirms they've accepted the offer from the company that
    selected them. Once both sides confirm, is_placed flips to true —
    this sends the student to the Hall of Fame and blocks further applies."""
    if _require_student() is None:
        return error("Student profile not found.", 403)
    profile = _get_profile(g.user.id) or {}
    if not profile.get("selected_by_company_id") or not profile.get("company_confirmed_hire"):
        return error("No pending selection to confirm.", 400)

    updates = {"student_confirmed_hire": True, "is_placed": True}
    supabase_admin.table("profiles").update(updates).eq("id", g.user.id).execute()
    return jsonify({"message": "Placement confirmed. Welcome to the Hall of Fame!"})


@app.route("/api/hall-of-fame", methods=["GET"])
def hall_of_fame():
    """Public list of placed students for the Hall of Fame page."""
    placed = (
        supabase_admin.table("profiles")
        .select("id, full_name, college, photo_url, qualified_domain, selected_by_company_id")
        .eq("is_placed", True)
        .execute()
    ).data or []
    company_ids = list({p["selected_by_company_id"] for p in placed if p.get("selected_by_company_id")})
    companies = {}
    if company_ids:
        rows = supabase_admin.table("companies").select("id, name").in_("id", company_ids).execute().data or []
        companies = {r["id"]: r["name"] for r in rows}
    out = [
        {**p, "company_name": companies.get(p.get("selected_by_company_id"), "")}
        for p in placed
    ]
    return jsonify({"placed": out})


@app.route("/api/company/candidates", methods=["GET"])
@login_required
def company_candidates():
    """
    Returns every student who has cleared the 2-round assessment
    (assessment_status = 'eligible'), for the company dashboard's
    candidate grid. Filtering by domain/search/shortlist happens
    client-side in dashboard.html.
    """
    if _require_company() is None:
        return error("Company profile not found.", 403)

    company_id = _get_company_id_for_user(g.user.id)
    hidden_ids = set()
    if company_id:
        hidden = (
            supabase_admin.table("company_hidden_candidates")
            .select("student_id")
            .eq("company_id", company_id)
            .execute()
        )
        hidden_ids = {row["student_id"] for row in hidden.data}

    result = (
        supabase_admin.table("profiles")
        .select(
            "id, full_name, college, degree, age, "
            "resume_url, photo_url, qualified_domain, assessment_status, updated_at, "
            "round1_correct, round1_total, round1_breakdown, round2_correct, round2_total, employment_type, experience_years, salary_expectation, "
            "is_placed, selected_by_company_id, company_confirmed_hire"
        )
        .eq("role", "student")
        .eq("assessment_status", "eligible")
        .order("updated_at", desc=True)
        .execute()
    )
    candidates = [c for c in result.data if c["id"] not in hidden_ids]

    # Backfill: students who passed before round1_correct/round1_total
    # existed on `profiles` (schema_score_fields.sql) are correctly
    # marked eligible, but show a blank score because it was never
    # copied onto their profile row. Their real score is still sitting
    # in assessment_attempts (the row where they passed), so pull it
    # from there instead of leaving the candidate card blank.
    missing_score_ids = [
        c["id"] for c in candidates
        if c.get("round1_correct") is None or c.get("round1_total") is None
    ]
    if missing_score_ids:
        attempts = (
            supabase_admin.table("assessment_attempts")
            .select("student_id, domain, round1_correct, round1_total, round1_breakdown, "
                    "round2_correct, round2_total, overall_passed, created_at")
            .in_("student_id", missing_score_ids)
            .eq("overall_passed", True)
            .order("created_at", desc=True)
            .execute()
        ).data or []
        # Keep only the most recent passing attempt per student (already
        # sorted desc, so first match wins) and only when it matches the
        # domain they're currently qualified in.
        latest_pass_by_student = {}
        for a in attempts:
            latest_pass_by_student.setdefault(a["student_id"], a)

        for c in candidates:
            if c["id"] not in latest_pass_by_student:
                continue
            a = latest_pass_by_student[c["id"]]
            if c.get("round1_correct") is None:
                c["round1_correct"] = a.get("round1_correct")
            if c.get("round1_total") is None:
                c["round1_total"] = a.get("round1_total")
            if c.get("round1_breakdown") is None:
                c["round1_breakdown"] = a.get("round1_breakdown")
            if c.get("round2_correct") is None:
                c["round2_correct"] = a.get("round2_correct")
            if c.get("round2_total") is None:
                c["round2_total"] = a.get("round2_total")

    return jsonify({"candidates": candidates})


@app.route("/api/company/test-stats", methods=["GET"])
@login_required
def company_test_stats():
    """Dashboard stat: total registered students. Both attended_test and
    total_users are the same count (per request)."""
    if _require_company() is None:
        return error("Company profile not found.", 403)

    all_students = (
        supabase_admin.table("profiles").select("id", count="exact").eq("role", "student").execute()
    )
    total_users = all_students.count if all_students.count is not None else len(all_students.data or [])

    return jsonify({"total_users": total_users, "attended_test": total_users})


@app.route("/api/company/hide-candidate", methods=["POST"])
@login_required
def hide_candidate():
    """Removes a candidate from THIS company's dashboard only. Body: { student_id }."""
    if _require_company() is None:
        return error("Company profile not found.", 403)

    payload = request.get_json(silent=True) or {}
    missing = require_fields(payload, ["student_id"])
    if missing:
        return error(f"Missing field(s): {', '.join(missing)}.", 422)

    company_id = _get_company_id_for_user(g.user.id)
    if not company_id:
        return error("No linked company record found for this login.", 403)

    try:
        supabase_admin.table("company_hidden_candidates").upsert(
            {"company_id": company_id, "student_id": payload["student_id"]},
            on_conflict="company_id,student_id",
        ).execute()
    except Exception as e:
        return error(str(e), 400)

    return jsonify({"message": "Candidate removed from your dashboard."})


@app.route("/api/student/apply", methods=["POST"])
@login_required
def apply_to_company():
    """
    Student applies to a tie-up company. Body:
    { "company_id": "...", "role": "SDE", "answers": {"Field label": "value", ...} }
    Stores one row per (company, student, role); re-applying updates it.
    """
    if _require_student() is None:
        return error("Student profile not found.", 403)

    profile = _get_profile(g.user.id) or {}
    if profile.get("assessment_status") != "eligible":
        return error("You must clear the technical assessment before applying.", 403)
    if profile.get("is_placed"):
        return error("You've already been placed through PGI Job Portal and can't apply further.", 403)

    payload = request.get_json(silent=True) or {}
    missing = require_fields(payload, ["company_id"])
    if missing:
        return error(f"Missing field(s): {', '.join(missing)}.", 422)

    answers = payload.get("answers") or {}
    if not isinstance(answers, dict):
        return error("answers must be an object.", 422)

    row = {
        "company_id": payload["company_id"],
        "student_id": g.user.id,
        "role": str(payload.get("role") or "").strip() or None,
        "answers": answers,
    }
    try:
        supabase_admin.table("applications").upsert(
            row, on_conflict="company_id,student_id,role"
        ).execute()
    except Exception as e:
        return error(f"Could not submit application: {e}", 500)

    return jsonify({"message": "Application submitted."})


@app.route("/api/company/applications", methods=["GET"])
@login_required
def company_applications():
    """Returns every student who has applied to this company, newest first."""
    if _require_company() is None:
        return error("Company profile not found.", 403)
    company_id = _get_company_id_for_user(g.user.id)
    if not company_id:
        return jsonify({"applications": []})

    apps = (
        supabase_admin.table("applications")
        .select("*")
        .eq("company_id", company_id)
        .order("created_at", desc=True)
        .execute()
    ).data or []
    if not apps:
        return jsonify({"applications": []})

    student_ids = list({a["student_id"] for a in apps})
    students = (
        supabase_admin.table("profiles")
        .select("id, full_name, college, degree, age, resume_url, photo_url, assessment_status")
        .in_("id", student_ids)
        .eq("assessment_status", "eligible")
        .execute()
    ).data or []
    by_id = {s["id"]: s for s in students}

    out = []
    for a in apps:
        student = by_id.get(a["student_id"])
        if not student:
            continue
        out.append({**student, "role": a.get("role"), "answers": a.get("answers") or {},
                    "applied_at": a.get("created_at")})
    return jsonify({"applications": out})


@app.route("/api/company/shortlist", methods=["GET"])
@login_required
def get_shortlist():
    """Returns the list of student IDs this company has starred."""
    if _require_company() is None:
        return error("Company profile not found.", 403)

    company_id = _get_company_id_for_user(g.user.id)
    if not company_id:
        return jsonify({"student_ids": []})

    result = (
        supabase_admin.table("company_shortlist")
        .select("student_id")
        .eq("company_id", company_id)
        .execute()
    )
    return jsonify({"student_ids": [row["student_id"] for row in result.data]})


@app.route("/api/company/shortlist", methods=["POST"])
@login_required
def add_shortlist():
    """Stars a candidate for this company. Body: { student_id }."""
    if _require_company() is None:
        return error("Company profile not found.", 403)

    payload = request.get_json(silent=True) or {}
    missing = require_fields(payload, ["student_id"])
    if missing:
        return error(f"Missing field(s): {', '.join(missing)}.", 422)

    company_id = _get_company_id_for_user(g.user.id)
    if not company_id:
        return error("No linked company record found for this login.", 403)

    try:
        supabase_admin.table("company_shortlist").upsert(
            {"company_id": company_id, "student_id": payload["student_id"]},
            on_conflict="company_id,student_id",
        ).execute()
    except Exception as e:
        return error(str(e), 400)

    return jsonify({"message": "Shortlisted."})


@app.route("/api/company/shortlist/<student_id>", methods=["DELETE"])
@login_required
def remove_shortlist(student_id):
    """Un-stars a candidate for this company."""
    if _require_company() is None:
        return error("Company profile not found.", 403)

    company_id = _get_company_id_for_user(g.user.id)
    if not company_id:
        return error("No linked company record found for this login.", 403)

    supabase_admin.table("company_shortlist").delete().eq("company_id", company_id).eq(
        "student_id", student_id
    ).execute()

    return jsonify({"message": "Removed from shortlist."})


@app.route("/api/admin/selected-candidates", methods=["GET"])
def admin_selected_candidates():
    """
    Cumulative, cross-company view of every candidate shortlisted so far —
    grouped by company. For internal verification only, so it's protected
    by the same admin secret as /api/admin/company/provision, not by a
    normal login.

    Example:
        curl http://localhost:8000/api/admin/selected-candidates \\
          -H "X-Admin-Secret: your-secret"
    """
    x_admin_secret = request.headers.get("X-Admin-Secret")
    if not ADMIN_PROVISION_SECRET or x_admin_secret != ADMIN_PROVISION_SECRET:
        return error("Unauthorized.", 401)

    shortlist_rows = supabase_admin.table("company_shortlist").select("*").execute().data or []
    if not shortlist_rows:
        return jsonify({"companies": []})

    company_ids = list({row["company_id"] for row in shortlist_rows})
    student_ids = list({row["student_id"] for row in shortlist_rows})

    companies_result = (
        supabase_admin.table("companies").select("id, name").in_("id", company_ids).execute()
    )
    companies_by_id = {c["id"]: c for c in (companies_result.data or [])}

    students_result = (
        supabase_admin.table("profiles")
        .select("id, full_name, college, degree, age, mobile_number, resume_url, qualified_domain")
        .in_("id", student_ids)
        .execute()
    )
    students_by_id = {s["id"]: s for s in (students_result.data or [])}

    grouped = {}
    for row in shortlist_rows:
        cid = row["company_id"]
        company = companies_by_id.get(cid, {"id": cid, "name": "Unknown Company"})
        student = students_by_id.get(row["student_id"])
        if not student:
            continue
        grouped.setdefault(cid, {"company_name": company["name"], "candidates": []})
        grouped[cid]["candidates"].append(student)

    return jsonify({"companies": list(grouped.values())})


@app.route("/api/company/unlock-contact", methods=["POST"])
@login_required
def company_unlock_contact():
    """Reveals a student's mobile + email to the logged-in company and records
    the unlock (once per company/student) so the admin can track it."""
    if _require_company() is None:
        return error("Company profile not found.", 403)
    company_id = _get_company_id_for_user(g.user.id)
    if not company_id:
        return error("No linked company record found.", 404)
    student_id = (request.get_json(silent=True) or {}).get("student_id")
    if not student_id:
        return error("student_id required.", 422)
    st = (supabase_admin.table("profiles").select("id, mobile_number, assessment_status")
          .eq("id", student_id).eq("role", "student").maybe_single().execute())
    st = st.data if st else None
    if not st or st.get("assessment_status") != "eligible":
        return error("Student not found.", 404)
    email = None
    try:
        email = supabase_admin.auth.admin.get_user_by_id(student_id).user.email
    except Exception as e:
        print("unlock-contact email lookup failed:", repr(e))
    try:
        seen = (supabase_admin.table("contact_unlocks").select("id")
                .eq("company_id", company_id).eq("student_id", student_id).execute().data)
        if not seen:
            supabase_admin.table("contact_unlocks").insert(
                {"company_id": company_id, "student_id": student_id}).execute()
    except Exception as e:
        print("unlock-contact log failed:", repr(e))
        return error("Could not unlock contact right now.", 500)
    return jsonify({"mobile_number": st.get("mobile_number"), "email": email})


# ============================== ADMIN DASHBOARD (added) ==============================

DOMAIN_NAMES = {
    "swe": "Software Engineering", "ds": "Data Science & AI", "cloud": "Cloud Computing",
    "cyber": "Cybersecurity", "mobile": "Mobile App Development", "solar": "Solar Engineering",
    "hr": "HR & Talent Acquisition", "marketing": "Marketing & Brand Management",
    "sales": "Sales & Business Development", "finance": "Finance & Accounting",
    "ops": "Operations & Supply Chain",
}


def _fetch_all(table: str, columns: str = "*", page: int = 1000):
    """Reads every row of a table (Supabase caps a single query at ~1000 rows).
    A missing table/column returns [] so one bad table never breaks the dashboard."""
    rows, start = [], 0
    try:
        while True:
            chunk = (supabase_admin.table(table).select(columns)
                     .range(start, start + page - 1).execute().data or [])
            rows.extend(chunk)
            if len(chunk) < page:
                break
            start += page
    except Exception as e:
        print(f"dashboard: could not read {table}:", repr(e))
    return rows


def _parse_ts(v):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None


@app.route("/api/admin/dashboard-stats", methods=["GET"])
def admin_dashboard_stats():
    """Live, portal-wide numbers for admin-dashboard.html. Protected by the
    same X-Admin-Secret as the other admin routes. Read-only."""
    x_admin_secret = request.headers.get("X-Admin-Secret")
    if not ADMIN_PROVISION_SECRET or x_admin_secret != ADMIN_PROVISION_SECRET:
        return error("Unauthorized.", 401)

    from collections import Counter
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    d7, d30 = now - timedelta(days=7), now - timedelta(days=30)

    profiles = _fetch_all("profiles")
    students = [p for p in profiles if p.get("role") == "student"]
    companies = _fetch_all("companies")
    attempts = _fetch_all("assessment_attempts")
    violations = _fetch_all("assessment_violations")
    applications = _fetch_all("applications")
    selections = _fetch_all("company_selections")
    shortlist = _fetch_all("company_shortlist")
    messages = _fetch_all("messages")
    payments = _fetch_all("assessment_payments")
    requests_ = _fetch_all("company_access_requests")

    # ---- students ----
    logins = [_parse_ts(s.get("last_login")) for s in students]
    logins = [t for t in logins if t]
    status = Counter((s.get("assessment_status") or "locked") for s in students)
    attended_ids = {a["student_id"] for a in attempts if a.get("student_id")}
    stu = {
        "total": len(students),
        "logged_in": len(logins),
        "active_7d": sum(1 for t in logins if t >= d7),
        "active_30d": sum(1 for t in logins if t >= d30),
        "attended_test": len(attended_ids),
        "not_attended": max(len(students) - len(attended_ids), 0),
        "eligible": status.get("eligible", 0),
        "failed": status.get("failed", 0),
        "locked": status.get("locked", 0),
        "placed": sum(1 for s in students if s.get("placed")),
        "resume_uploaded": sum(1 for s in students if s.get("resume_url")),
        "photo_uploaded": sum(1 for s in students if s.get("photo_url")),
    }

    # ---- companies ----
    hiring = [c for c in companies if c.get("has_openings")]
    apps_by_co = Counter(a.get("company_id") for a in applications)
    sel_by_co = Counter(s.get("company_id") for s in selections)
    comp = {
        "total": len(companies),
        "hiring": len(hiring),
        "not_hiring": len(companies) - len(hiring),
        "open_roles": sum(len(c.get("roles") or []) for c in hiring),
        "pending_requests": sum(1 for r in requests_ if (r.get("status") or "pending") == "pending"),
        "total_requests": len(requests_),
        "list": sorted([{
            "name": c.get("name"), "hiring": bool(c.get("has_openings")),
            "roles": c.get("roles") or [], "applications": apps_by_co.get(c.get("id"), 0),
            "selected": sel_by_co.get(c.get("id"), 0),
        } for c in companies], key=lambda x: (not x["hiring"], -x["applications"], x["name"] or "")),
    }

    # ---- domains ----
    dom = {k: {"id": k, "name": v, "attempts": 0, "passed": 0, "students": set(), "qualified": 0}
           for k, v in DOMAIN_NAMES.items()}
    by_name = {v.lower(): k for k, v in DOMAIN_NAMES.items()}
    for a in attempts:
        raw = str(a.get("domain") or "").strip()
        key = raw if raw in dom else by_name.get(raw.lower(), raw)
        d = dom.setdefault(key, {"id": key, "name": raw or "Unknown", "attempts": 0,
                                 "passed": 0, "students": set(), "qualified": 0})
        d["attempts"] += 1
        d["passed"] += 1 if a.get("overall_passed") else 0
        d["students"].add(a.get("student_id"))
    for s in students:
        raw = str(s.get("qualified_domain") or "").strip()
        key = raw if raw in dom else by_name.get(raw.lower())
        if key in dom and s.get("assessment_status") == "eligible":
            dom[key]["qualified"] += 1
    domains = [{"id": d["id"], "name": d["name"], "attempts": d["attempts"], "passed": d["passed"],
                "students": len(d["students"]), "qualified": d["qualified"],
                "pass_rate": round(d["passed"] * 100 / d["attempts"], 1) if d["attempts"] else 0}
               for d in dom.values()]
    domains.sort(key=lambda x: -x["attempts"])

    # ---- tests ----
    scores = [a["round1_score"] for a in attempts if a.get("round1_score") is not None]
    passed = sum(1 for a in attempts if a.get("overall_passed"))
    vtypes = Counter(v.get("violation_type") for v in violations)
    tests = {
        "total_attempts": len(attempts), "passed": passed, "failed": len(attempts) - passed,
        "pass_rate": round(passed * 100 / len(attempts), 1) if attempts else 0,
        "avg_round1": round(sum(scores) / len(scores), 1) if scores else 0,
        "flagged": sum(1 for a in attempts if a.get("flagged")),
        "violations_total": len(violations), "violation_types": dict(vtypes),
    }

    # ---- engagement / revenue ----
    paid = [p for p in payments if p.get("status") in ("paid", "consumed")]
    extra = {
        "applications": len(applications), "selections": len(selections),
        "confirmed_placements": sum(1 for s in selections if s.get("student_confirmed")),
        "shortlisted": len(shortlist), "messages": len(messages),
        "retest_payments": len(paid),
        "revenue_inr": round(sum((p.get("amount_paise") or 0) for p in paid) / 100, 2),
    }

    # ---- 14-day trends ----
    days = [(now - timedelta(days=i)).date() for i in range(13, -1, -1)]
    def series(rows, field):
        c = Counter()
        for r in rows:
            t = _parse_ts(r.get(field))
            if t:
                c[t.date()] += 1
        return [c.get(d, 0) for d in days]
    trends = {
        "labels": [d.strftime("%d %b") for d in days],
        "signups": series(students, "created_at"),
        "attempts": series(attempts, "created_at"),
        "applications": series(applications, "created_at"),
    }

    # ---- top colleges / degrees ----
    colleges = Counter((s.get("college") or "").strip() for s in students if (s.get("college") or "").strip())
    top_colleges = [{"name": n, "count": c} for n, c in colleges.most_common(8)]

    # ---- recent activity ----
    name_by_id = {p["id"]: p.get("full_name") or "Student" for p in profiles}
    recent = sorted(attempts, key=lambda a: a.get("created_at") or "", reverse=True)[:10]
    recent_attempts = [{
        "student": name_by_id.get(a.get("student_id"), "Student"),
        "domain": DOMAIN_NAMES.get(a.get("domain"), a.get("domain")),
        "score": a.get("round1_score"), "passed": bool(a.get("overall_passed")),
        "flagged": bool(a.get("flagged")), "at": a.get("created_at"),
    } for a in recent]

    return jsonify({
        "generated_at": now.isoformat(), "students": stu, "companies": comp,
        "domains": domains, "domains_total": len(DOMAIN_NAMES), "tests": tests,
        "extra": extra, "trends": trends, "top_colleges": top_colleges,
        "recent_attempts": recent_attempts,
    })


def _admin_ok():
    s = request.headers.get("X-Admin-Secret")
    return bool(ADMIN_PROVISION_SECRET) and s == ADMIN_PROVISION_SECRET


@app.route("/api/admin/outreach", methods=["GET"])
def admin_outreach():
    """Which students companies have contacted (chat / shortlist / selection)
    and which student profiles companies have viewed (profile_views table)."""
    if not _admin_ok():
        return error("Unauthorized.", 401)
    from collections import defaultdict
    prof = {p["id"]: p for p in _fetch_all("profiles", "id,full_name,college,qualified_domain")}
    comp = {c["id"]: c.get("name") for c in _fetch_all("companies", "id,name")}
    contacted = defaultdict(set)          # student -> company ids that reached out
    by_co = defaultdict(lambda: {"contacted": set(), "viewed": set(), "views": 0})
    for m in _fetch_all("messages"):
        if m.get("sender_role") == "company":
            contacted[m["student_id"]].add(m["company_id"]); by_co[m["company_id"]]["contacted"].add(m["student_id"])
    for t in ("company_shortlist", "company_selections"):
        for r in _fetch_all(t):
            if r.get("student_id") and r.get("company_id"):
                contacted[r["student_id"]].add(r["company_id"]); by_co[r["company_id"]]["contacted"].add(r["student_id"])
    unl_st, unl_co = defaultdict(int), defaultdict(int)
    for u in _fetch_all("contact_unlocks"):
        unl_st[u["student_id"]] += 1; unl_co[u["company_id"]] += 1
        contacted[u["student_id"]].add(u["company_id"]); by_co[u["company_id"]]["contacted"].add(u["student_id"])
    views = defaultdict(int)
    for v in _fetch_all("profile_views"):
        views[v["student_id"]] += 1
        by_co[v["company_id"]]["viewed"].add(v["student_id"]); by_co[v["company_id"]]["views"] += 1
    ids = set(contacted) | set(views) | set(unl_st)
    students = sorted([{
        "name": (prof.get(i) or {}).get("full_name") or "Student",
        "college": (prof.get(i) or {}).get("college") or "",
        "domain": DOMAIN_NAMES.get((prof.get(i) or {}).get("qualified_domain"), (prof.get(i) or {}).get("qualified_domain") or ""),
        "contacted_by": sorted(comp.get(c, "Company") for c in contacted.get(i, [])),
        "views": views.get(i, 0), "unlocked": unl_st.get(i, 0),
    } for i in ids], key=lambda s: (-len(s["contacted_by"]), -s["views"]))[:200]
    companies = sorted([{
        "name": comp.get(cid, "Company"), "contacted": len(d["contacted"]),
        "viewed": len(d["viewed"]), "views": d["views"], "unlocked": unl_co.get(cid, 0),
    } for cid, d in by_co.items()], key=lambda c: (-c["contacted"], -c["views"]))
    return jsonify({
        "students_contacted": len(contacted),
        "companies_active": sum(1 for d in by_co.values() if d["contacted"]),
        "profiles_viewed": sum(1 for v in views.values() if v),
        "total_views": sum(views.values()),
        "total_unlocks": sum(unl_co.values()), "students_unlocked": len(unl_st),
        "students": students, "companies": companies,
    })


@app.route("/api/admin/payments", methods=["GET"])
def admin_payments():
    """Who paid the retest fee, how many times, and every transaction (read-only)."""
    if not _admin_ok():
        return error("Unauthorized.", 401)
    from collections import defaultdict
    rows = _fetch_all("assessment_payments")
    prof = {p["id"]: p for p in _fetch_all("profiles", "id,full_name,college")}
    emails = {}
    try:
        for u in supabase_admin.auth.admin.list_users(per_page=1000):
            emails[u.id] = u.email
    except Exception as e:
        print("admin_payments: could not list emails:", repr(e))
    paid = [r for r in rows if r.get("status") in ("paid", "consumed")]
    per = defaultdict(lambda: {"times": 0, "used": 0, "left": 0, "paise": 0, "last": ""})
    for r in paid:
        d = per[r["student_id"]]
        d["times"] += 1; d["paise"] += r.get("amount_paise") or 0
        d["used"] += 1 if r["status"] == "consumed" else 0
        d["left"] += 1 if r["status"] == "paid" else 0
        d["last"] = max(d["last"], r.get("created_at") or "")
    students = sorted([{
        "name": (prof.get(i) or {}).get("full_name") or "Student", "college": (prof.get(i) or {}).get("college") or "",
        "email": emails.get(i, ""), "times": d["times"], "used": d["used"], "left": d["left"],
        "amount_inr": round(d["paise"] / 100, 2), "last": d["last"],
    } for i, d in per.items()], key=lambda x: (x["times"], x["last"]), reverse=True)
    txns = sorted([{
        "student": (prof.get(r["student_id"]) or {}).get("full_name") or "Student",
        "order_id": r.get("razorpay_order_id"), "payment_id": r.get("razorpay_payment_id"),
        "amount_inr": round((r.get("amount_paise") or 0) / 100, 2), "status": r["status"], "at": r.get("created_at"),
    } for r in paid], key=lambda x: x["at"] or "", reverse=True)[:300]
    return jsonify({
        "total_payments": len(paid), "revenue_inr": round(sum(r.get("amount_paise") or 0 for r in paid) / 100, 2),
        "students_paid": len(per), "repeat_payers": sum(1 for d in per.values() if d["times"] > 1),
        "credits_left": sum(d["left"] for d in per.values()),
        "abandoned": sum(1 for r in rows if r.get("status") == "created"),
        "students": students, "transactions": txns,
    })


@app.route("/api/admin/chats", methods=["GET"])
def admin_chats():
    """All company <-> student conversations (one row per pair)."""
    if not _admin_ok():
        return error("Unauthorized.", 401)
    prof = {p["id"]: p.get("full_name") or "Student" for p in _fetch_all("profiles", "id,full_name")}
    comp = {c["id"]: c.get("name") or "Company" for c in _fetch_all("companies", "id,name")}
    pairs = {}
    for m in _fetch_all("messages"):
        k = (m["company_id"], m["student_id"])
        c = pairs.setdefault(k, {"count": 0, "last": "", "last_at": "", "last_role": ""})
        c["count"] += 1
        if (m.get("created_at") or "") >= c["last_at"]:
            c.update(last=m.get("body") or "", last_at=m.get("created_at") or "", last_role=m.get("sender_role"))
    out = [{"company_id": k[0], "student_id": k[1], "company": comp.get(k[0], "Company"),
            "student": prof.get(k[1], "Student"), **v} for k, v in pairs.items()]
    out.sort(key=lambda x: x["last_at"], reverse=True)
    return jsonify({"conversations": out})


@app.route("/api/admin/chats/messages", methods=["GET"])
def admin_chat_messages():
    if not _admin_ok():
        return error("Unauthorized.", 401)
    cid, sid = request.args.get("company_id"), request.args.get("student_id")
    if not cid or not sid:
        return error("company_id and student_id required.", 422)
    try:
        rows = (supabase_admin.table("messages").select("sender_role, body, created_at")
                .eq("company_id", cid).eq("student_id", sid).order("created_at").execute().data or [])
    except Exception as e:
        return error(f"Could not load messages: {e}", 500)
    return jsonify({"messages": rows})


if __name__ == "__main__":
    # Matches the API_BASE your HTML files hardcode: http://localhost:8000
    app.run(host="127.0.0.1", port=8000, debug=True, use_reloader=False, threaded=False)
