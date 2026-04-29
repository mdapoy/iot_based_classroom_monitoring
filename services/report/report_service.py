from docx import Document
from repositories.supabase_client import supabase
from datetime import datetime
from reportlab.platypus import SimpleDocTemplate, Paragraph
from reportlab.lib.styles import getSampleStyleSheet

TABLE = "reports"

def generate_docx(summary: str, output_path: str):
    doc = Document()
    
    doc.add_heading("SUMMARY", level=1)
    doc.add_paragraph(summary)

    doc.save(output_path)
    return output_path

def insert_metadata(data: dict, file_path: str):
    payload = {
        "tanggal": data["tanggal"],
        "jam": data["jam"],
        "ruangan": data["ruangan"],
        "kode_matkul": data["kode_matkul"],
        "kode_dosen": data["kode_dosen"],
        "kelas": data["kelas"],
        "transcription_done": True,
        "file_path": file_path,
        "created_at": datetime.utcnow().isoformat()
    }

    supabase.table(TABLE).insert(payload).execute()

def insert_summary_record(report_id: int, file_path: str):
    supabase.table("summary").insert({
        "reports_id": report_id,
        "file_path": file_path
    }).execute()

def generate_pdf(summary: str, output_path: str):
    doc = SimpleDocTemplate(output_path)
    styles = getSampleStyleSheet()

    content = []
    content.append(Paragraph("<b>SUMMARY</b>", styles["Title"]))
    content.append(Paragraph(summary, styles["BodyText"]))

    doc.build(content)

    return output_path

def get_existing_summary(report_id: int):
    res = supabase.table("summary") \
        .select("*") \
        .eq("reports_id", report_id) \
        .execute()

    return res.data[0] if res.data else None
