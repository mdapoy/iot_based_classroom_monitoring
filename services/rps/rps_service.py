import os
from datetime import date, timedelta
from dotenv import load_dotenv
from repositories.supabase_client import supabase
from core.logger import logger

load_dotenv()

SEMESTER_START_DATE = os.getenv("SEMESTER_START_DATE", "2026-02-23")

# Tanggal Senin dari minggu-minggu yang TIDAK dihitung sebagai pertemuan kuliah
# (FS, UTS, libur panjang, dll.) — diisi di .env, pisah koma
# Contoh: SKIP_WEEKS=2026-03-16,2026-05-25
_raw_skip = os.getenv("SKIP_WEEKS", "2026-03-16")
SKIP_WEEK_MONDAYS = set()
for s in _raw_skip.split(","):
    s = s.strip()
    if s:
        try:
            SKIP_WEEK_MONDAYS.add(date.fromisoformat(s))
        except ValueError:
            logger.warning(f"[RPS] Format SKIP_WEEKS salah, abaikan: '{s}'")


def _get_week_monday(d: date) -> date:
    """Kembalikan tanggal Senin dari minggu yang sama dengan d."""
    return d - timedelta(days=d.weekday())


def get_meeting_week(tanggal_str: str) -> int:
    """
    Hitung pertemuan ke-N berdasarkan tanggal rekaman, tanggal mulai semester,
    dan daftar minggu yang di-skip (FS, UTS, libur panjang).

    Cara kerja:
      Iterasi dari minggu pertama sampai minggu rekaman.
      Setiap minggu yang ada di SKIP_WEEK_MONDAYS tidak dihitung.

    Contoh (semester mulai 23 Feb 2026, FS minggu 16 Mar):
      Rekaman 23 Mar 2026 → pertemuan ke-4 (bukan 5)
    """
    recording_date = date.fromisoformat(str(tanggal_str))
    semester_start = date.fromisoformat(SEMESTER_START_DATE)

    # Senin dari minggu rekaman dan minggu mulai semester
    rec_monday    = _get_week_monday(recording_date)
    start_monday  = _get_week_monday(semester_start)

    if rec_monday < start_monday:
        logger.warning(f"[RPS] Tanggal rekaman {tanggal_str} sebelum semester mulai")
        return 1

    pertemuan = 0
    current = start_monday

    while current <= rec_monday:
        if current not in SKIP_WEEK_MONDAYS:
            pertemuan += 1
        current += timedelta(weeks=1)

    return max(1, pertemuan)


def get_rps_for_week(kode_matkul: str, pertemuan_ke: int) -> dict | None:
    """
    Ambil data RPS dari tabel rps_pertemuan untuk pertemuan tertentu.
    Return None jika tidak ditemukan.
    """
    try:
        res = (
            supabase.table("rps_pertemuan")
            .select("*")
            .eq("kode_matkul", kode_matkul)
            .eq("pertemuan_ke", pertemuan_ke)
            .single()
            .execute()
        )

        return res.data if res.data else None

    except Exception as e:
        logger.warning(
            f"[RPS] Not found | kode_matkul={kode_matkul} pertemuan={pertemuan_ke} | {e}"
        )
        return None
