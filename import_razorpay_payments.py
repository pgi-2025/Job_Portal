"""Copies retest payments that Razorpay captured but the database never saved
(e.g. paid through the old payment link) into Supabase 'assessment_payments'.
Students are matched by the email or mobile number used while paying.
Usage:  python import_razorpay_payments.py          (preview only)
        python import_razorpay_payments.py --apply  (save to database)"""
import sys, re, requests
from datetime import datetime, timezone
from app import supabase_admin, RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET

APPLY = "--apply" in sys.argv
AMOUNTS = {100, 900}  # Rs 1 (testing) and Rs 9

def fetch_payments():
    out, skip = [], 0
    while True:
        r = requests.get("https://api.razorpay.com/v1/payments", params={"count": 100, "skip": skip},
                         auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET), timeout=30)
        r.raise_for_status()
        items = r.json().get("items", [])
        out += items
        if len(items) < 100:
            return out
        skip += 100

def last10(v): return re.sub(r"\D", "", v or "")[-10:]

def match_student(p, by_email, by_phone):
    return by_email.get((p.get("email") or "").lower()) or by_phone.get(last10(p.get("contact")))

if __name__ == "__main__":
    by_email, page = {}, 1
    while True:
        users = supabase_admin.auth.admin.list_users(page=page, per_page=1000)
        for u in users:
            by_email[(u.email or "").lower()] = u.id
        if len(users) < 1000:
            break
        page += 1
    profs = supabase_admin.table("profiles").select("id, mobile_number").eq("role", "student").execute().data or []
    by_phone = {last10(x.get("mobile_number")): x["id"] for x in profs if last10(x.get("mobile_number"))}
    rows = supabase_admin.table("assessment_payments").select("id, razorpay_order_id, razorpay_payment_id, status").execute().data or []
    known_pay = {r["razorpay_payment_id"] for r in rows if r.get("razorpay_payment_id")}
    by_order = {r["razorpay_order_id"]: r for r in rows}
    added = fixed = 0
    for p in fetch_payments():
        if p.get("status") != "captured" or p.get("amount") not in AMOUNTS or p["id"] in known_pay:
            continue
        order = by_order.get(p.get("order_id"))
        if order:  # app created the order but never marked it paid
            if order["status"] == "created":
                print("FIX  ", p["id"], "-> order", p["order_id"], "marked paid")
                if APPLY:
                    supabase_admin.table("assessment_payments").update({"status": "paid", "razorpay_payment_id": p["id"]}).eq("id", order["id"]).execute()
                fixed += 1
            continue
        sid = match_student(p, by_email, by_phone)
        label = f'{p["id"]}  Rs{p["amount"]/100:g}  {p.get("email")}  {p.get("contact")}'
        if not sid:
            print("NO MATCH", label, "(use recover_payment.py with the student's email)"); continue
        print("ADD  ", label)
        if APPLY:
            supabase_admin.table("assessment_payments").insert({
                "student_id": sid, "razorpay_order_id": p.get("order_id") or f"manual_{p['id']}",
                "razorpay_payment_id": p["id"], "amount_paise": p["amount"], "status": "paid",
                "created_at": datetime.fromtimestamp(p["created_at"], timezone.utc).isoformat()}).execute()
        added += 1
    print(f"\n{'Saved' if APPLY else 'Preview:'} {added} new, {fixed} fixed.", "" if APPLY else "Run again with --apply to save.")
