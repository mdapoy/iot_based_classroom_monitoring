from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from services.recorder.video_recorder import start_video_recording, stop_video_recording #perubahan nama import dari utilities ke services dan perumbahan nama file dari recorder.py ke video_recorder.py    
from repositories.supabase_client import supabase
from datetime import datetime, timedelta

scheduler = BackgroundScheduler()
scheduler.start()


def schedule_recording(start_datetime, stop_datetime):

    scheduler.add_job(
        start_video_recording,
        'date',
        run_date=start_datetime,
        id=f"start_{start_datetime}",
        replace_existing=True
    )

    scheduler.add_job(
        stop_video_recording,
        "date",
        run_date=stop_datetime,
        id=f"stop_{stop_datetime}",
        replace_existing=True
    )

    return {
        "status": "schedule created",
        "start": str(start_datetime),
        "stop": str(stop_datetime)
    }

def get_schedules(): #baru

    response = supabase.table("jadwal_kuliah").select("*").execute()

    return response.data

def load_schedule_from_db():

    print("Loading schedules from database...")

    scheduler.remove_all_jobs()

    response = supabase.table("jadwal_kuliah").select("*").execute()

    schedules = response.data

    for item in schedules:

        start_dt = get_next_datetime(item["hari"], item["jam_mulai"])
        stop_dt = get_next_datetime(item["hari"], item["jam_selesai"])

        # buat date sendiri
        date_now = datetime.now().strftime("%Y%m%d")

        nama_file = (
            f"{item['hari']}_"
            f"{item['jam_mulai'].replace(':','')}_"
            f"{item['ruangan']}_"
            f"{item['kode_mata_kuliah']}_"
            f"{item['dosen_utama']}_"
            f"{item['kelas']}_"
            f"{date_now}"
        )

        print("Scheduling recording:", nama_file)

        scheduler.add_job(
            start_video_recording,
            "date",
            run_date=start_dt,
            args=[nama_file],
            id=f"start_{nama_file}",
            replace_existing=True
        )

        scheduler.add_job(
            stop_video_recording,
            "date",
            run_date=stop_dt,
            id=f"stop_{nama_file}",
            replace_existing=True
        )

def get_next_datetime(hari, waktu):

    hari_map = {
        "senin": 0,
        "selasa": 1,
        "rabu": 2,
        "kamis": 3,
        "jumat": 4,
        "sabtu": 5,
        "minggu": 6
    }

    now = datetime.now()

    target_day = hari_map[hari.lower()]

    days_ahead = target_day - now.weekday()

    if days_ahead < 0:
        days_ahead += 7

    target_date = now + timedelta(days=days_ahead)

    # 🔧 konversi string → time
    if isinstance(waktu, str):
        waktu = datetime.strptime(waktu, "%H:%M:%S").time()

    return datetime.combine(target_date.date(), waktu)

def find_jadwal(kode_matkul, kode_dosen, kelas):

    res = (
        supabase
        .table("jadwal_kuliah")
        .select("*")
        .eq("kode_mata_kuliah", kode_matkul)
        .eq("dosen_utama", kode_dosen)
        .eq("kelas", kelas)
        .execute()
    )

    if len(res.data) == 0:
        return None

    return res.data[0]