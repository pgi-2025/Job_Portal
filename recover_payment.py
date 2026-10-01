"""One-time: credit a student for a Razorpay payment that was captured but never recorded
(for example, one paid through the old payment link).
Usage:  python recover_payment.py pay_XXXXXXXX student@email.com [--dry]
It asks Razorpay (server-to-server) to confirm the payment is captured for the right amount."""
import sys, requests
from app import supabase_admin, RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RETEST_FEE_PAISE

args = [a for a in sys.argv[1:] if not a.startswith("--")]
if len(args) != 2:
    sys.exit(__doc__)
pay_id, email = args
r = requests.get(f"https://api.razorpay.com/v1/payments/{pay_id}", auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET), timeout=20)
if r.status_code != 200:
    sys.exit(f"Razorpay could not find {pay_id} (HTTP {r.status_code}). Check the ID and that the server uses the same live/test key.")
p = r.json()
print("Razorpay says:", p.get("status"), p.get("amount"), "paise via", p.get("method"))
if p.get("status") != "captured" or p.get("amount") != RETEST_FEE_PAISE:
    sys.exit("Not captured, or the amount does not match RETEST_FEE_PAISE. Nothing changed.")
user = next((u for u in supabase_admin.auth.admin.list_users(per_page=1000) if (u.email or "").lower() == email.lower()), None)
if not user:
    sys.exit("No student with that email.")
if supabase_admin.table("assessment_payments").select("id").eq("razorpay_payment_id", pay_id).execute().data:
    sys.exit("This payment is already recorded. Nothing to do.")
row = {"student_id": user.id, "razorpay_order_id": p.get("order_id") or f"manual_{pay_id}",
       "razorpay_payment_id": pay_id, "amount_paise": p["amount"], "status": "paid"}
if "--dry" in sys.argv:
    sys.exit(f"Dry run OK - would credit {email}.")
supabase_admin.table("assessment_payments").upsert(row, on_conflict="razorpay_order_id").execute()
print("Done. The student can now start the retest (one credit).")
