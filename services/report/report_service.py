import os
import re
from docx import Document
from repositories.supabase_client import supabase
from datetime import datetime
from reportlab.platypus import SimpleDocTemplate, Paragraph, PageBreak, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm

TABLE = "reports"


def _md_to_rl(text: str) -> str:
    """
    Konversi subset Markdown ke format yang bisa dibaca ReportLab Paragraph.

    Transformasi:
      **teks**  → <b>teks</b>    (bold)
      *teks*    → <i>teks</i>    (italic, single asterisk)
      1. teks   → teks           (hapus penomoran di awal baris)
      - teks    → teks           (hapus bullet di awal baris)
      # Judul   → teks           (hapus hash heading)

    ReportLab mendukung subset HTML: <b>, <i>, <u>, <br/>.
    Karakter khusus XML (<, >, &) di-escape agar tidak crash parser.
    """
    if not text:
        return text

    # Escape XML characters DULU sebelum inject tag HTML
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")

    # **bold** → <b>bold</b>  (bold dulu sebelum italic agar **..** tidak terpotong)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)

    # *italic* → <i>italic</i>
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text, flags=re.DOTALL)

    # Hapus penomoran di awal baris: "1. ", "2. ", dst
    text = re.sub(r'(?m)^\d+\.\s+', '', text)

    # Hapus bullet di awal baris: "- ", "* "
    text = re.sub(r'(?m)^[-*]\s+', '', text)

    # Hapus hash heading: "# ", "## ", dst
    text = re.sub(r'(?m)^#+\s*', '', text)

    return text.strip()

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

def _fmt_sec(sec: float) -> str:
    """Konversi detik ke format Xm Ys."""
    sec   = int(sec or 0)
    m, s  = divmod(sec, 60)
    return f"{m}m {s}s" if m else f"{s}s"


def generate_combined_pdf(
    summary: str,
    analysis: dict,
    output_path: str,
    nama_matkul: str = "MATA KULIAH",
    pertemuan_ke: int = 0,
) -> str:
    """
    Buat PDF 2 halaman:
      Halaman 1 — Ringkasan materi (judul dinamis sesuai matkul & pertemuan)
      Halaman 2 — Analisis kesesuaian RPS:
                  A. Kesesuaian Materi
                  B. Aktivitas Pembelajaran
                  C. Kesesuaian Metode Pembelajaran
    """
    dir_name = os.path.dirname(output_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    doc    = SimpleDocTemplate(output_path)
    styles = getSampleStyleSheet()
    content = []

    # ── HALAMAN 1: RINGKASAN ────────────────────────────────────────
    judul = f"RINGKASAN MATERI {nama_matkul.upper()} - PERTEMUAN KE-{pertemuan_ke}"
    content.append(Paragraph(f"<b>{judul}</b>", styles["Title"]))
    content.append(Spacer(1, 0.5 * cm))
    content.append(Paragraph(_md_to_rl(summary), styles["BodyText"]))

    content.append(PageBreak())

    # ── HALAMAN 2: ANALISIS ─────────────────────────────────────────
    content.append(Paragraph("<b>ANALISIS KESESUAIAN RPS</b>", styles["Title"]))
    content.append(Spacer(1, 0.3 * cm))
    content.append(Paragraph(
        f"Pertemuan ke-{analysis.get('pertemuan_ke', '-')}",
        styles["Heading2"]
    ))
    content.append(Spacer(1, 0.3 * cm))

    # ── A. KESESUAIAN MATERI ────────────────────────────────────────
    content.append(Paragraph("<b>A. Kesesuaian Materi</b>", styles["Heading3"]))
    content.append(Spacer(1, 0.15 * cm))
    content.append(Paragraph(
        f"<b>Materi Pembelajaran RPS  :</b> {_md_to_rl(analysis.get('materi_pembelajaran', '-'))}",
        styles["BodyText"]
    ))
    content.append(Spacer(1, 0.1 * cm))
    content.append(Paragraph(
        f"<b>Kesesuaian Materi        :</b> {_md_to_rl(analysis.get('kesesuaian', '-'))}",
        styles["BodyText"]
    ))
    content.append(Spacer(1, 0.1 * cm))
    content.append(Paragraph(
        f"<b>Pertemuan Terdeteksi     :</b> ke-{_md_to_rl(str(analysis.get('pertemuan_terdeteksi', '-')))}",
        styles["BodyText"]
    ))
    content.append(Spacer(1, 0.1 * cm))
    content.append(Paragraph(
        f"<b>Status Waktu             :</b> {_md_to_rl(analysis.get('status_waktu', '-'))}",
        styles["BodyText"]
    ))
    content.append(Spacer(1, 0.1 * cm))
    content.append(Paragraph(
        f"<b>Penjelasan               :</b> {_md_to_rl(analysis.get('penjelasan', '-'))}",
        styles["BodyText"]
    ))
    content.append(Spacer(1, 0.35 * cm))

    # ── B. AKTIVITAS PEMBELAJARAN ───────────────────────────────────
    activity = analysis.get("activity")
    content.append(Paragraph("<b>B. Aktivitas Pembelajaran</b>", styles["Heading3"]))
    content.append(Spacer(1, 0.15 * cm))

    if activity and activity.get("dominant"):
        content.append(Paragraph(
            f"<b>Aktivitas Dominan        :</b> {activity.get('dominant', '-')}",
            styles["BodyText"]
        ))
        content.append(Spacer(1, 0.1 * cm))
        content.append(Paragraph(
            f"<b>Ceramah                  :</b> "
            f"{activity.get('ceramah_pct', 0)}%  ({_fmt_sec(activity.get('ceramah_sec', 0))})",
            styles["BodyText"]
        ))
        content.append(Spacer(1, 0.05 * cm))
        content.append(Paragraph(
            f"<b>Tanya Jawab              :</b> "
            f"{activity.get('tanya_jawab_pct', 0)}%  ({_fmt_sec(activity.get('tanya_jawab_sec', 0))})",
            styles["BodyText"]
        ))
        content.append(Spacer(1, 0.05 * cm))
        content.append(Paragraph(
            f"<b>Diskusi                  :</b> "
            f"{activity.get('diskusi_pct', 0)}%  ({_fmt_sec(activity.get('diskusi_sec', 0))})",
            styles["BodyText"]
        ))
        content.append(Spacer(1, 0.05 * cm))
        content.append(Paragraph(
            f"<b>Diam                     :</b> "
            f"{activity.get('diam_pct', 0)}%  ({_fmt_sec(activity.get('diam_sec', 0))})",
            styles["BodyText"]
        ))

        # Detail timeline
        timeline = activity.get("activity_timeline", [])
        if timeline:
            content.append(Spacer(1, 0.15 * cm))
            content.append(Paragraph("<b>Detail Timeline:</b>", styles["BodyText"]))
            content.append(Spacer(1, 0.05 * cm))
            for seg in timeline:
                def fmt_sec(s):
                    s = int(s or 0)
                    return f"{s // 60:02d}:{s % 60:02d}"
                label = (
                    f"  {fmt_sec(seg.get('start_sec', 0))} – "
                    f"{fmt_sec(seg.get('end_sec', 0))}  "
                    f"[{seg.get('activity', '-')}]  "
                    f"{seg.get('description', '')}"
                )
                content.append(Paragraph(label, styles["BodyText"]))
                content.append(Spacer(1, 0.04 * cm))
    else:
        content.append(Paragraph(
            "Data aktivitas tidak tersedia.",
            styles["BodyText"]
        ))

    content.append(Spacer(1, 0.35 * cm))

    # ── C. KESESUAIAN METODE PEMBELAJARAN ──────────────────────────
    content.append(Paragraph("<b>C. Kesesuaian Metode Pembelajaran</b>", styles["Heading3"]))
    content.append(Spacer(1, 0.15 * cm))
    content.append(Paragraph(
        f"<b>Metode RPS (Pengalaman)  :</b> {_md_to_rl(analysis.get('pengalaman_rps', '-'))}",
        styles["BodyText"]
    ))
    content.append(Spacer(1, 0.1 * cm))
    content.append(Paragraph(
        f"<b>Aktivitas Dominan Aktual :</b> {_md_to_rl(analysis.get('metode_dominan', '-'))}",
        styles["BodyText"]
    ))
    content.append(Spacer(1, 0.1 * cm))
    content.append(Paragraph(
        f"<b>Kesesuaian Metode        :</b> {_md_to_rl(analysis.get('kesesuaian_metode', '-'))}",
        styles["BodyText"]
    ))
    content.append(Spacer(1, 0.1 * cm))
    content.append(Paragraph(
        f"<b>Penjelasan               :</b> {_md_to_rl(analysis.get('penjelasan_metode', '-'))}",
        styles["BodyText"]
    ))

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
