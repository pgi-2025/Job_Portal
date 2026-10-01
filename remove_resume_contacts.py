"""One-time cleanup: removes each student's mobile number + email (and the
'Contact' heading) from resume PDFs that were saved BEFORE the update.
Run once from the project folder:  pip install pymupdf  &&  python remove_resume_contacts.py
Add --dry to only show what would change."""
import sys
import fitz  # pymupdf
from app import supabase_admin

DRY = "--dry" in sys.argv


def clean(pdf_bytes, terms):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    hits = 0
    for page in doc:
        rects = [r for t in terms for r in page.search_for(t)]
        rects += [r for r in page.search_for("Contact") if r.x0 < 200]  # sidebar heading only
        for r in rects:
            page.add_redact_annot(r, fill=False)
        if rects:
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
            hits += len(rects)
    return (doc.tobytes(garbage=3, deflate=True), hits) if hits else (None, 0)


students = supabase_admin.table("profiles").select("id, full_name, mobile_number, resume_url") \
    .eq("role", "student").not_.is_("resume_url", "null").execute().data or []
done = 0
for s in students:
    path = s["resume_url"].split("/resumes/")[-1].split("?")[0]
    terms = [t for t in [(s.get("mobile_number") or "").strip()] if t]
    try:
        email = supabase_admin.auth.admin.get_user_by_id(s["id"]).user.email
        if email:
            terms.append(email)
        data = supabase_admin.storage.from_("resumes").download(path)
        out, hits = clean(data, terms)
        if not out:
            print("skip (already clean):", s["full_name"]); continue
        print(("would clean" if DRY else "cleaned"), s["full_name"], f"({hits} spots)")
        if not DRY:
            supabase_admin.storage.from_("resumes").upload(
                path, out, {"content-type": "application/pdf", "upsert": "true"})
        done += 1
    except Exception as e:
        print("FAILED:", s.get("full_name"), repr(e))
print(f"\nDone. {done} resume(s) {'would be ' if DRY else ''}updated out of {len(students)}.")
