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

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})  # tighten to your real frontend origin(s) in production



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
        except Exception:
            return error("Invalid or expired session.", 401)
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

    return jsonify({
        "access_token": session.access_token,
        "refresh_token": session.refresh_token,
        "role": role,
        "profile": profile,
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
    return jsonify({"profile": profile})


@app.route("/api/student/profile", methods=["PUT"])
@login_required
def update_profile():
    """Saves profile edits (name, mobile, college, degree, age) to the database."""
    if _require_student() is None:
        return error("Student profile not found.", 403)

    payload = request.get_json(silent=True) or {}
    allowed_fields = ["full_name", "mobile_number", "college", "degree", "age",
                       "resume_summary", "resume_skills", "resume_experience", "resume_projects"]
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
            "round1_passed": payload.get("round1_passed"),
            "round2_passed": payload.get("round2_passed"),
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

    supabase_admin.table("profiles").update(updates).eq("id", g.user.id).execute()

    return jsonify({"message": "Assessment result saved.", "status": updates["assessment_status"]})


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
        .select("id, name, roles, has_openings")
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

    if not updates:
        return error("Nothing to update.", 422)

    try:
        supabase_admin.table("companies").update(updates).eq("id", company_id).execute()
    except Exception as e:
        print("update_company_openings error:", repr(e))
        return error(f"Could not save openings: {e}", 500)

    result = (
        supabase_admin.table("companies")
        .select("id, name, roles, has_openings")
        .eq("id", company_id)
        .maybe_single()
        .execute()
    )
    return jsonify({"message": "Openings updated.", "company": result.data})


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
            "resume_url, photo_url, qualified_domain, assessment_status, updated_at"
        )
        .eq("role", "student")
        .eq("assessment_status", "eligible")
        .order("updated_at", desc=True)
        .execute()
    )
    candidates = [c for c in result.data if c["id"] not in hidden_ids]
    return jsonify({"candidates": candidates})


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


if __name__ == "__main__":
    # Matches the API_BASE your HTML files hardcode: http://localhost:8000
    app.run(host="127.0.0.1", port=8000, debug=True)
