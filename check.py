import os, requests
from dotenv import load_dotenv
load_dotenv()
u = os.environ.get("SUPABASE_URL", "").rstrip("/")
k = os.environ.get("SUPABASE_ANON_KEY", "")
print("URL:", u)
try:
    r = requests.get(u + "/auth/v1/health", headers={"apikey": k}, timeout=15)
    print("Status:", r.status_code, r.text[:200])
    print("OK - Supabase is reachable." if r.status_code == 200 else "Supabase answered but with a problem (project paused or wrong key).")
except Exception as e:
    print("CANNOT REACH SUPABASE:", type(e).__name__, e)
    print("-> Project is paused, or your network/VPN/firewall is blocking it.")
