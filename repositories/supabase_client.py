from supabase import create_client
import os
from dotenv import load_dotenv

load_dotenv()

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")

if not url or not key:
    raise RuntimeError(
        "Environment variables SUPABASE_URL and SUPABASE_KEY must be set. "
        "Please configure them in Railway dashboard under Variables."
    )

supabase = create_client(url, key)