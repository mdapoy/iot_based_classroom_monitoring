import os
from docx import Document
from repositories.supabase_client import supabase
from datetime import datetime
from reportlab.platypus import SimpleDocTemplate, Paragraph, PageBreak, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm

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
    dir_name = os.path.dirname(output_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    doc = SimpleDocTemplate(output_path)
    styles = getSampleStyleSheet()

    content = []
    content.append(Paragraph("<b>SUMMARY</b>", styles["Title"]))
    content.append(Paragraph(summary, styles["BodyText"]))

    doc.build(content)

    return output_path

def generate_combined_pdf(summary: str, analysis: dict, output_path: str) -> str:
    """
    Buat PDF 2 halaman:
      Halaman 1 — Ringkasan materi perkuliahan
      Halaman 2 — Analisis kesesuaian RPS
    """
    dir_name = os.path.dirname(output_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    doc = SimpleDocTemplate(output_path)
    styles = getSampleStyleSheet()
    content = []

    # ── HALAMAN 1: RINGKASAN ────────────────────────────────
    content.append(Paragraph("<b>RINGKASAN MATERI PERKULIAHAN</b>", styles["Title"]))
    content.append(Spacer(1, 0.5 * cm))
    content.append(Paragraph(summary, styles["BodyText"]))

    content.append(PageBreak())

    # ── HALAMAN 2: ANALISIS RPS ─────────────────────────────
    content.append(Paragraph("<b>ANALISIS KESESUAIAN RPS</b>", styles["Title"]))
    content.append(Spacer(1, 0.4 * cm))
    content.append(Paragraph(
        f"Pertemuan ke-{analysis.get('pertemuan_ke', '-')}",
        styles["Heading2"]
    ))
    content.append(Spacer(1, 0.3 * cm))
    content.append(Paragraph(
        f"<b>Materi Pembelajaran RPS:</b> {analysis.get('materi_pembelajaran', '-')}",
        styles["BodyText"]
    ))
    content.append(Spacer(1, 0.2 * cm))
    content.append(Paragraph(
        f"<b>Kesesuaian Materi:</b> {analysis.get('kesesuaian', '-')}",
        styles["BodyText"]
    ))
    content.append(Spacer(1, 0.2 * cm))
    content.append(Paragraph(
        f"<b>Kesesuaian Materi:</b> {analysis.get('status_waktu', '-')}",
        styles["BodyText"]
    ))
    content.append(Spacer(1, 0.3 * cm))
    content.append(Paragraph("<b>Penjelasan:</b>", styles["Heading3"]))
    content.append(Paragraph(analysis.get("penjelasan", "-"), styles["BodyText"]))

    doc.build(content)
    return output_path


def get_existing_summary(report_id: int):
    res = supabase.table("reports") \
        .select("summary_path") \
        .eq("id", report_id) \
        .execute()

    if not res.data:
        return None

    row = res.data[0]

    if not row.get("summary_path"):
        return None

    return {
        "file_path": row["summary_path"]
    }
