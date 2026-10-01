-- Run this once in Supabase SQL editor to support the new resume builder fields.
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS resume_summary text;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS resume_skills text;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS resume_experience text;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS resume_projects text;

-- Lets companies mark whether they currently have openings (shown as a
-- green/red dot on the student dashboard's company cards).
ALTER TABLE companies ADD COLUMN IF NOT EXISTS has_openings boolean DEFAULT false;

-- The backend's service_role needs write access to companies for the new
-- "Manage Openings" feature (companydb.html) to save roles/has_openings.
GRANT SELECT, INSERT, UPDATE, DELETE ON public.companies TO service_role;

-- Company logos (shown on the student dashboard's tie-up company cards).
ALTER TABLE companies ADD COLUMN IF NOT EXISTS logo_url text;
-- Create a public "logos" Storage bucket in Supabase (Storage > New bucket,
-- name it exactly "logos", make it public) — same as the existing
-- "photos"/"resumes" buckets — so uploaded logos are viewable.

-- In-platform chat between a company and a student, so interviews / Google
-- Meet links stay inside the portal instead of sharing phone numbers.
CREATE TABLE IF NOT EXISTS public.messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id uuid NOT NULL REFERENCES public.companies(id),
  student_id uuid NOT NULL REFERENCES public.profiles(id),
  sender_role text NOT NULL CHECK (sender_role IN ('company','student')),
  body text NOT NULL,
  created_at timestamptz DEFAULT now()
);
ALTER TABLE public.messages ENABLE ROW LEVEL SECURITY;
-- No policies needed: the backend only ever talks to this table using the
-- service_role key, which bypasses RLS; enabling RLS with zero policies
-- simply blocks any direct anon/authenticated access.
GRANT SELECT, INSERT, UPDATE, DELETE ON public.messages TO service_role;

-- Lets a company remove a candidate from ONLY its own dashboard (the
-- candidate still shows up for every other company until they do the same).
CREATE TABLE IF NOT EXISTS public.company_hidden_candidates (
  company_id uuid NOT NULL REFERENCES public.companies(id),
  student_id uuid NOT NULL REFERENCES public.profiles(id),
  created_at timestamptz DEFAULT now(),
  PRIMARY KEY (company_id, student_id)
);
ALTER TABLE public.company_hidden_candidates ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.company_hidden_candidates TO service_role;
