"""
evaluation_service.py
---------------------
Generator PDF Laporan Evaluasi Dosen.

Layout multi-halaman:
  Hal 1        — Header · Info Card · Section A (Ringkasan Kinerja)
  Hal 2 … N+1  — Section B … N+1   (Rincian per Mata Kuliah)
  Hal terakhir — Section Kesimpulan & Rekomendasi
"""

import os

from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle

from core.logger import logger

# ── Path aset ────────────────────────────────────────────────────────────────
_LOGO_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "assets", "klaktify-icon.png"
)

# ── Palette (sama dengan laporan identifikasi) ────────────────────────────────
C_PRI  = HexColor("#7B1C2E")   # Telkom dark red
C_GOLD = HexColor("#C8A84B")   # gold / cukup
C_GRN  = HexColor("#2E7D32")   # green / sesuai
C_BLU  = HexColor("#1976D2")   # blue
C_LBL  = HexColor("#999999")   # muted label
C_BG   = HexColor("#F5F5F5")   # alt row / card bg
C_BDR  = HexColor("#DDDDDD")   # border
C_TXT  = HexColor("#1A1A1A")   # body text
C_BG2  = HexColor("#FFF9F5")   # note box bg

# ── Page geometry ─────────────────────────────────────────────────────────────
PAGE_W, PAGE_H = A4   # 595.28 × 841.89 pt
ML = 50.0             # margin left
MR = PAGE_W - 50.0    # margin right
CW = MR - ML          # content width ≈ 495 pt
FOOTER_Y = 40.0       # safe bottom boundary


# ══════════════════════════════════════════════════════════════════════════════
# Helper private — ParagraphStyle factory
# ══════════════════════════════════════════════════════════════════════════════

_ps_idx = [0]

def _ps(**kw) -> ParagraphStyle:
    _ps_idx[0] += 1
    d = dict(
        name=f"_eval_{_ps_idx[0]}",
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=C_TXT,
        spaceAfter=0,
        spaceBefore=0,
    )
    d.update(kw)
    return ParagraphStyle(**d)


# ══════════════════════════════════════════════════════════════════════════════
# Helper: draw wrapped Paragraph, return new y (bottom edge)
# ══════════════════════════════════════════════════════════════════════════════

def _para(c, txt: str, st: ParagraphStyle, x: float, y: float, w: float) -> float:
    p = Paragraph(str(txt or "-"), st)
    _, h = p.wrap(w, 9999)
    p.drawOn(c, x, y - h)
    return y - h


# ══════════════════════════════════════════════════════════════════════════════
# Helper: section header bar with badge letter
# ══════════════════════════════════════════════════════════════════════════════

def _sec_hdr(c, badge: str, title: str, y: float) -> float:
    H = 26
    c.setFillColor(C_PRI)
    c.rect(ML, y - H, CW, H, fill=1, stroke=0)
    BS = 16
    bx = ML + 7
    by = y - H + (H - BS) / 2
    c.setFillColor(white)
    c.rect(bx, by, BS, BS, fill=1, stroke=0)
    c.setFillColor(C_PRI)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(bx + BS / 2, by + 4, badge)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(bx + BS + 8, y - H + 9, title)
    return y - H - 6


# ══════════════════════════════════════════════════════════════════════════════
# Helper: footer
# ══════════════════════════════════════════════════════════════════════════════

def _footer(c, pg: int, tot: int) -> None:
    c.setFillColor(C_LBL)
    c.setFont("Helvetica", 7.5)
    c.drawString(
        ML, 28,
        "Laporan ini dibuat secara otomatis oleh sistem Clactify -- Telkom University"
    )
    c.drawRightString(MR, 28, f"Hal. {pg} / {tot}")


# ══════════════════════════════════════════════════════════════════════════════
# Helper: running header (halaman 2+)
# ══════════════════════════════════════════════════════════════════════════════

def _running_hdr(c, nama_dosen: str) -> float:
    """Draw top accent bar + running title. Return y setelah header."""
    y = PAGE_H - 10
    LOGO_H = LOGO_W = 13
    c.setFillColor(C_PRI)
    c.rect(ML, y - 4, CW, 4, fill=1, stroke=0)
    y -= 4
    label = f"LAPORAN EVALUASI DOSEN  |  {nama_dosen.upper()}"
    c.setFillColor(C_TXT)
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(ML, y - 14, label)
    logo_label = "CLACTIFY"
    lw = c.stringWidth(logo_label, "Helvetica-Bold", 7.5)
    if os.path.exists(_LOGO_PATH):
        c.drawImage(
            _LOGO_PATH,
            MR - lw - LOGO_W - 4, y - 18,
            width=LOGO_W, height=LOGO_H,
            preserveAspectRatio=True, mask="auto",
        )
    c.setFillColor(C_PRI)
    c.setFont("Helvetica-Bold", 7.5)
    c.drawRightString(MR, y - 14, logo_label)
    return y - 30


# ══════════════════════════════════════════════════════════════════════════════
# Helper: 4-column info card (header)
# Kolom: NAMA DOSEN | PROGRAM STUDI | SEMESTER | PERIODE ANALISIS
# ══════════════════════════════════════════════════════════════════════════════

def _info_card_4(
    c,
    nama_dosen: str,
    prodi: str,
    semester_label: str,
    periode_label: str,
    y: float,
) -> float:
    H   = 72   # was 60 — diperbesar agar jarak label–value lebih nyaman
    CW4 = CW / 4
    c.setFillColor(white)
    c.setStrokeColor(C_BDR)
    c.setLineWidth(0.5)
    c.rect(ML, y - H, CW, H, fill=1, stroke=1)

    # Strip "(Pasca-UTS)" / "(Pra-UTS)" agar periode lebih ringkas di kotak
    periode_display = periode_label.split("(")[0].strip()

    items = [
        ("NAMA DOSEN",       nama_dosen),
        ("PROGRAM STUDI",    prodi),
        ("SEMESTER",         semester_label),
        ("PERIODE ANALISIS", periode_display),
    ]
    for i, (label, val) in enumerate(items):
        cx = ML + i * CW4 + 8
        if i > 0:
            c.setStrokeColor(C_BDR)
            c.setLineWidth(0.5)
            c.line(ML + i * CW4, y - 5, ML + i * CW4, y - H + 5)
        c.setFillColor(C_LBL)
        c.setFont("Helvetica", 7)
        c.drawString(cx, y - 13, label)          # was y - 16
        # Value: Paragraph agar bisa wrap; jarak dari label diperbesar
        p = Paragraph(
            str(val or "-"),
            _ps(fontName="Helvetica-Bold", fontSize=9, leading=13, textColor=C_TXT)
        )
        _, th = p.wrap(CW4 - 16, 9999)
        p.drawOn(c, cx, y - 28 - th)             # top value di y-28 (was y-20)

    return y - H


# ══════════════════════════════════════════════════════════════════════════════
# Helper: KPI card (4 kolom) — Section A
# Kolom: TOTAL MATA KULIAH DIAMPU | TOTAL PERTEMUAN DIANALISIS |
#        STATUS KINERJA KESELURUHAN | PERIODE ANALISIS
# ══════════════════════════════════════════════════════════════════════════════

def _kpi_card(
    c,
    total_matkul: int,
    total_pertemuan: int,
    status_kinerja: str,
    periode_label: str,
    y: float,
) -> float:
    H   = 60
    CW4 = CW / 4

    status_color = {
        "Baik":            C_GRN,
        "Cukup":           C_GOLD,
        "Perlu Perhatian": C_PRI,
    }.get(status_kinerja, C_TXT)

    c.setFillColor(C_BG)
    c.setStrokeColor(C_BDR)
    c.setLineWidth(0.5)
    c.rect(ML, y - H, CW, H, fill=1, stroke=1)

    cols = [
        ("TOTAL MATA KULIAH DIAMPU",    str(total_matkul),                    C_TXT),
        ("TOTAL PERTEMUAN DIANALISIS",  str(total_pertemuan),                  C_TXT),
        ("STATUS KINERJA KESELURUHAN",  status_kinerja,                        status_color),
        ("PERIODE ANALISIS",            periode_label.split("(")[0].strip(),    C_BLU),
    ]
    for i, (lbl, val, vc) in enumerate(cols):
        cx = ML + i * CW4 + 8
        if i > 0:
            c.setStrokeColor(C_BDR)
            c.setLineWidth(0.5)
            c.line(ML + i * CW4, y - 5, ML + i * CW4, y - H + 5)
        c.setFillColor(C_LBL)
        c.setFont("Helvetica", 7)
        c.drawString(cx, y - 14, lbl)
        c.setFillColor(vc)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(cx, y - 38, val)

    return y - H


# ══════════════════════════════════════════════════════════════════════════════
# Helper: 4-column card per matkul (Section B+)
# Kolom: MATA KULIAH | KODE | METODE DOMINAN | KESESUAIAN RPS
# ══════════════════════════════════════════════════════════════════════════════

def _matkul_card(
    c,
    nama_matkul: str,
    kode_matkul: str,
    metode_dominan: str,
    kesesuaian_rps: str,
    y: float,
) -> float:
    H   = 62   # was 55 — diperbesar agar nama matkul panjang bisa wrap
    CW4 = CW / 4

    kes_color = _kes_color(kesesuaian_rps)

    c.setFillColor(C_BG)
    c.setStrokeColor(C_BDR)
    c.setLineWidth(0.5)
    c.rect(ML, y - H, CW, H, fill=1, stroke=1)

    cols = [
        ("MATA KULIAH",    nama_matkul,                      C_TXT),   # tanpa _trunc, pakai Paragraph
        ("KODE",           kode_matkul,                       C_BLU),
        ("METODE DOMINAN", _trunc(metode_dominan, 22),        C_TXT),
        ("KESESUAIAN RPS", _fmt_pct(kesesuaian_rps),           kes_color),
    ]
    for i, (lbl, val, vc) in enumerate(cols):
        cx = ML + i * CW4 + 8
        if i > 0:
            c.setStrokeColor(C_BDR)
            c.setLineWidth(0.5)
            c.line(ML + i * CW4, y - 5, ML + i * CW4, y - H + 5)
        c.setFillColor(C_LBL)
        c.setFont("Helvetica", 7)
        c.drawString(cx, y - 14, lbl)

        if i == 0:
            # MATA KULIAH: Paragraph agar nama panjang bisa wrap ke baris bawah
            p_val = Paragraph(
                str(val or "-"),
                _ps(fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=vc)
            )
            _, th = p_val.wrap(CW4 - 16, 9999)
            p_val.drawOn(c, cx, y - 30 - th)    # top value di y-30
        else:
            c.setFillColor(vc)
            c.setFont("Helvetica-Bold", 11)
            c.drawString(cx, y - 38, val)

    return y - H


# ══════════════════════════════════════════════════════════════════════════════
# Helper: box catatan / kesimpulan dengan left accent border
# ══════════════════════════════════════════════════════════════════════════════

def _note_box(c, prefix: str, text: str, y: float, max_w: float = None) -> float:
    PAD, ACC = 10, 4
    w   = (max_w or CW)
    iw  = w - PAD * 2 - ACC
    txt = (f"<b>{prefix}</b> " if prefix else "") + (text or "-")
    st  = _ps(fontSize=8.5, leading=13, alignment=TA_JUSTIFY)
    p   = Paragraph(txt, st)
    _, th = p.wrap(iw, 9999)
    bh  = th + PAD * 2
    c.setFillColor(C_BG2)
    c.setStrokeColor(C_BDR)
    c.setLineWidth(0.5)
    c.rect(ML, y - bh, w, bh, fill=1, stroke=1)
    c.setFillColor(C_PRI)
    c.rect(ML, y - bh, ACC, bh, fill=1, stroke=0)
    p.drawOn(c, ML + ACC + PAD, y - bh + PAD)
    return y - bh


# ══════════════════════════════════════════════════════════════════════════════
# Helper: kesesuaian color
# ══════════════════════════════════════════════════════════════════════════════

def _fmt_pct(val) -> str:
    """Format kesesuaian: float/int → '75%', string kept as-is, None → '-'."""
    if val is None:
        return "-"
    try:
        return f"{float(val):.0f}%"
    except (TypeError, ValueError):
        s = str(val).strip()
        return s if s else "-"


def _kes_color(val):
    """Color for kesesuaian value: ≥80% green, ≥50% gold, <50% red. Handles float and string."""
    if val is None:
        return C_TXT
    try:
        pct = float(val)
        if pct >= 80:
            return C_GRN
        if pct >= 50:
            return C_GOLD
        return C_PRI
    except (TypeError, ValueError):
        pass
    v = str(val).upper()
    if "TIDAK" in v:
        return C_PRI
    if "SEBAGIAN" in v:
        return C_GOLD
    if "SESUAI" in v:
        return C_GRN
    return C_TXT


def _waktu_color(val: str):
    return C_GRN if "TEPAT" in (val or "").upper() else C_PRI


# ══════════════════════════════════════════════════════════════════════════════
# Helper: tabel ringkasan matkul (Section A)
# Kolom: KODE | MATA KULIAH | PERTEMUAN | TEPAT WAKTU | KESESUAIAN RPS
# ══════════════════════════════════════════════════════════════════════════════

_COL_A = [80, 155, 65, 80, 115]   # total = 495
_HDR_A = ["Kode", "Mata Kuliah", "Pertemuan", "Tepat Waktu", "Kesesuaian RPS"]

def _table_ringkasan(c, per_matkul: list[dict], y: float) -> float:
    ROW_H = 22
    HDR_H = 24

    # Gambar header
    c.setFillColor(C_PRI)
    c.rect(ML, y - HDR_H, CW, HDR_H, fill=1, stroke=0)
    x = ML
    for h, w in zip(_HDR_A, _COL_A):
        c.setFillColor(white)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(x + 5, y - HDR_H + 8, h)
        x += w
    y -= HDR_H

    # Gambar baris
    for i, m in enumerate(per_matkul):
        # Alternating background
        c.setFillColor(C_BG if i % 2 == 0 else white)
        c.setStrokeColor(C_BDR)
        c.setLineWidth(0.3)
        c.rect(ML, y - ROW_H, CW, ROW_H, fill=1, stroke=1)

        pct_tepat = float(m.get("pct_tepat_waktu") or 0)
        pct_color = C_GRN if pct_tepat >= 80 else (C_GOLD if pct_tepat >= 60 else C_PRI)

        cells = [
            (m.get("kode_matkul", "-"),               C_BLU,                                   False),
            (_trunc(m.get("nama_matkul", "-"), 30),    C_TXT,                                   False),
            (str(m.get("total_pertemuan", 0)),          C_TXT,                                   False),
            (f"{pct_tepat:.1f}%",                       pct_color,                               True),
            (_fmt_pct(m.get("kesesuaian_rps")),          _kes_color(m.get("kesesuaian_rps")),     True),
        ]
        x = ML
        for (text, color, bold), w in zip(cells, _COL_A):
            c.setFillColor(color)
            c.setFont("Helvetica-Bold" if bold else "Helvetica", 7.5)
            c.drawString(x + 5, y - ROW_H + 7, text)
            x += w

        y -= ROW_H

    return y


# ══════════════════════════════════════════════════════════════════════════════
# Helper: tabel per pertemuan (Section B+)
# Kolom: KE- | TOPIK | KESESUAIAN MATERI | DURASI | STATUS WAKTU |
#        AKTIVITAS PEMBELAJARAN | KESESUAIAN AKTIVITAS
# ══════════════════════════════════════════════════════════════════════════════

_COL_B = [28, 130, 68, 42, 65, 105, 57]   # total = 495 | Topik diperlebar 112→130
_HDR_B = ["Ke-", "Topik RPS", "Kes. Materi", "Durasi", "Status Waktu", "Aktivitas", "Kes. Metode"]

def _table_pertemuan(c, pertemuan_list: list[dict], y: float, check_fn) -> float:
    """
    Gambar tabel per pertemuan.
    Row height dinamis — dihitung dari tinggi Paragraph tertinggi per baris.

    check_fn(y_val, needed) → float
        Dipanggil sebelum setiap baris/header.
        Jika ruang tidak cukup, showPage() + reset y; return y baru.
        Jika cukup, return y_val tidak berubah.
    """
    MIN_ROW_H = 28   # tinggi minimum baris (pt)
    TOP_PAD   = 6    # padding atas konten di dalam sel
    HDR_H     = 24

    # ── Header tabel ──────────────────────────────────────────────────────────
    y = check_fn(y, HDR_H + MIN_ROW_H)
    c.setFillColor(C_PRI)
    c.rect(ML, y - HDR_H, CW, HDR_H, fill=1, stroke=0)
    x = ML
    for h, w in zip(_HDR_B, _COL_B):
        c.setFillColor(white)
        c.setFont("Helvetica-Bold", 7)
        c.drawString(x + 4, y - HDR_H + 8, h)
        x += w
    y -= HDR_H

    # ── Baris data ────────────────────────────────────────────────────────────
    for i, p in enumerate(pertemuan_list):

        # ── Pre-render Paragraph cells untuk dapat tinggi aktual ─────────────
        topik_txt = str(p.get("topik") or "-")
        akt_html  = str(p.get("aktivitas_str") or "-").replace(" · ", "<br/>")
        kes_t_txt = str(p.get("kesesuaian_metode") or "-")

        topik_st = _ps(fontSize=7, leading=10, textColor=C_TXT)
        akt_st   = _ps(fontSize=7, leading=10, textColor=C_TXT)
        kt_st    = _ps(fontSize=7, leading=10, fontName="Helvetica-Bold",
                       textColor=_kes_color(kes_t_txt))

        p_topik = Paragraph(topik_txt, topik_st)
        p_akt   = Paragraph(akt_html, akt_st)
        p_kt    = Paragraph(kes_t_txt, kt_st)

        _, topik_h = p_topik.wrap(_COL_B[1] - 8, 9999)
        _, akt_h   = p_akt.wrap(_COL_B[5] - 8, 9999)
        _, kt_h    = p_kt.wrap(_COL_B[6] - 8, 9999)

        # Tinggi baris = konten tertinggi + padding atas-bawah, min MIN_ROW_H
        ROW_H = max(MIN_ROW_H,
                    topik_h + TOP_PAD * 2,
                    akt_h   + TOP_PAD * 2,
                    kt_h    + TOP_PAD * 2)

        y = check_fn(y, ROW_H)

        # ── Background + border ───────────────────────────────────────────────
        c.setFillColor(C_BG if i % 2 == 0 else white)
        c.setStrokeColor(C_BDR)
        c.setLineWidth(0.3)
        c.rect(ML, y - ROW_H, CW, ROW_H, fill=1, stroke=1)

        mid   = ROW_H / 2
        dur   = f"{p.get('durasi_menit', 0)} mnt"
        kes_m_raw = p.get("kesesuaian_pct") if p.get("kesesuaian_pct") is not None else p.get("kesesuaian_materi")
        kes_m     = _fmt_pct(kes_m_raw)
        stat  = str(p.get("status_waktu") or "-")

        # ── Kolom 0: Ke- — plain, vertically centered ────────────────────────
        x = ML
        c.setFillColor(C_TXT)
        c.setFont("Helvetica", 7)
        c.drawString(x + 4, y - mid - 2, f"Ke-{p.get('pertemuan_ke', '-')}")
        x += _COL_B[0]

        # ── Kolom 1: Topik RPS — Paragraph full text, top-aligned ────────────
        p_topik.drawOn(c, x + 4, y - TOP_PAD - topik_h)
        x += _COL_B[1]

        # ── Kolom 2: Kes. Materi — plain, vertically centered ────────────────
        c.setFillColor(_kes_color(kes_m))
        c.setFont("Helvetica-Bold", 7)
        c.drawString(x + 4, y - mid - 2, kes_m)
        x += _COL_B[2]

        # ── Kolom 3: Durasi — plain, vertically centered ─────────────────────
        c.setFillColor(C_TXT)
        c.setFont("Helvetica", 7)
        c.drawString(x + 4, y - mid - 2, dur)
        x += _COL_B[3]

        # ── Kolom 4: Status Waktu — plain, vertically centered ───────────────
        c.setFillColor(_waktu_color(stat))
        c.setFont("Helvetica-Bold", 7)
        c.drawString(x + 4, y - mid - 2, stat)
        x += _COL_B[4]

        # ── Kolom 5: Aktivitas — Paragraph, vertically centered ──────────────
        p_akt.drawOn(c, x + 4, y - mid - akt_h / 2)
        x += _COL_B[5]

        # ── Kolom 6: Kes. Metode — Paragraph, vertically centered ────────────
        p_kt.drawOn(c, x + 4, y - mid - kt_h / 2)

        y -= ROW_H

    return y


# ══════════════════════════════════════════════════════════════════════════════
# Helper: truncate string
# ══════════════════════════════════════════════════════════════════════════════

def _trunc(s: str, n: int) -> str:
    return s[:n] + "…" if len(s) > n else s


# ══════════════════════════════════════════════════════════════════════════════
# Main public function
# ══════════════════════════════════════════════════════════════════════════════

def generate_evaluation_pdf(eval_data: dict, output_path: str) -> str:
    """
    Generate PDF laporan evaluasi dosen.

    Args:
        eval_data   : dict dari evaluation_analyzer.build_eval_data()
        output_path : path file PDF lokal (akan dibuat)

    Returns:
        output_path
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    dosen          = eval_data["dosen"]
    ringkasan      = eval_data["ringkasan"]
    detail_matkul  = eval_data["detail_matkul"]
    rekomendasi    = eval_data.get("rekomendasi", "")
    periode_label  = eval_data.get("periode_label", eval_data.get("periode", "-"))
    semester_label = eval_data.get("semester_label", "-")
    nama_dosen     = dosen.get("nama_lengkap", "-")
    prodi          = dosen.get("program_studi", "-")

    # Estimasi total halaman: 1 (header) + N matkul + 1 (kesimpulan)
    total_pages = 1 + len(detail_matkul) + 1

    # ── Canvas ────────────────────────────────────────────────────────────────
    c   = rl_canvas.Canvas(output_path, pagesize=A4)
    pg  = [1]   # mutable page counter
    cur = [PAGE_H - 35]   # mutable y cursor

    # ── Closure: new page ─────────────────────────────────────────────────────
    def new_page():
        _footer(c, pg[0], total_pages)
        c.showPage()
        pg[0] += 1
        cur[0] = _running_hdr(c, nama_dosen)

    # ── Closure: cek ruang, showPage jika kurang ──────────────────────────────
    def ensure(needed: float):
        if cur[0] < FOOTER_Y + needed:
            new_page()

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 1 — Header + Info Card + Section A
    # ══════════════════════════════════════════════════════════════════════════
    y = cur[0]

    # ── Main header row ───────────────────────────────────────────────────────
    LOGO_H = LOGO_W = 13
    c.setFillColor(C_PRI)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(ML, y, "UNIVERSITAS TELKOM")
    hdr_txt   = "CLACTIFY -- CLASS ACTIVITY IDENTIFY"
    hdr_txt_w = c.stringWidth(hdr_txt, "Helvetica", 8)
    if os.path.exists(_LOGO_PATH):
        c.drawImage(
            _LOGO_PATH,
            MR - hdr_txt_w - LOGO_W - 4, y - 4,
            width=LOGO_W, height=LOGO_H,
            preserveAspectRatio=True, mask="auto",
        )
    c.setFillColor(C_LBL)
    c.setFont("Helvetica", 8)
    c.drawRightString(MR, y, hdr_txt)
    y -= 32

    # ── Judul ─────────────────────────────────────────────────────────────────
    c.setFillColor(C_TXT)
    c.setFont("Helvetica-Bold", 24)
    c.drawString(ML, y, "LAPORAN EVALUASI DOSEN")
    y -= 20

    # Subtitle: "Rekap Kinerja Mengajar · {prodi} · {semester_label}"
    c.setFillColor(HexColor("#666666"))
    c.setFont("Helvetica", 8.5)
    c.drawString(ML, y, f"Rekap Kinerja Mengajar  ·  {prodi}  ·  {semester_label}")
    y -= 10

    # Separator
    c.setStrokeColor(C_BDR)
    c.setLineWidth(0.5)
    c.line(ML, y, MR, y)
    y -= 12

    # ── Info Card (4 kolom): NAMA DOSEN | PROGRAM STUDI | SEMESTER | PERIODE ANALISIS
    y = _info_card_4(c, nama_dosen, prodi, semester_label, periode_label, y)
    y -= 14

    # ── Section A: Ringkasan Kinerja ──────────────────────────────────────────
    y = _sec_hdr(c, "A", "RINGKASAN KINERJA MENGAJAR", y)
    y -= 12

    # Sub-header tabel
    c.setFillColor(C_TXT)
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(ML, y, "Rekapitulasi Kinerja per Mata Kuliah")
    y -= 10

    # Tabel ringkasan
    y = _table_ringkasan(c, ringkasan["per_matkul"], y)

    # ── Footer hal 1 ──────────────────────────────────────────────────────────
    _footer(c, pg[0], total_pages)
    c.showPage()
    pg[0] += 1

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 2 … N+1 — Satu halaman per Mata Kuliah
    # ══════════════════════════════════════════════════════════════════════════
    for sec_idx, matkul in enumerate(detail_matkul):
        # Setiap matkul selalu mulai di halaman baru
        cur[0] = _running_hdr(c, nama_dosen)
        y = cur[0]

        badge = chr(ord("B") + sec_idx)   # B, C, D, …
        nama_matkul = matkul.get("nama_matkul", "-")

        # Section header
        y = _sec_hdr(c, badge, f"DETAIL MATA KULIAH: {nama_matkul.upper()}", y)
        y -= 10

        # 4-col card: MATA KULIAH | KODE | METODE DOMINAN | KESESUAIAN RPS
        y = _matkul_card(
            c,
            nama_matkul    = matkul.get("nama_matkul", "-"),
            kode_matkul    = matkul.get("kode_matkul", "-"),
            metode_dominan = matkul.get("metode_dominan", "-"),
            kesesuaian_rps = matkul.get("kesesuaian_rps", "-"),
            y              = y,
        )
        y -= 14

        # Sub-header tabel
        c.setFillColor(C_TXT)
        c.setFont("Helvetica-Bold", 9.5)
        c.drawString(ML, y, f"{badge}.1  Ringkasan Per Pertemuan")
        y -= 8

        # check_fn: (y_val, needed) → float
        # Jika tidak ada ruang, showPage + running header, return y baru.
        def _check(y_val: float, needed: float) -> float:
            if y_val < FOOTER_Y + needed:
                _footer(c, pg[0], total_pages)
                c.showPage()
                pg[0] += 1
                return _running_hdr(c, nama_dosen)
            return y_val

        # Tabel per pertemuan
        y = _table_pertemuan(c, matkul.get("pertemuan", []), y, _check)
        y -= 14

        # Kesimpulan box
        kesimpulan = matkul.get("kesimpulan") or "-"
        kes_height = _estimate_para_height(
            f"<b>Kesimpulan:</b> {kesimpulan}", CW - 24, 8.5, 13
        ) + 20
        y = _check(y, kes_height)

        # Sub-header kesimpulan
        c.setFillColor(C_TXT)
        c.setFont("Helvetica-Bold", 9.5)
        c.drawString(ML, y, f"{badge}.2  Kesimpulan Mata Kuliah")
        y -= 8

        y = _note_box(c, "", kesimpulan, y)

        _footer(c, pg[0], total_pages)
        c.showPage()
        pg[0] += 1

    # ══════════════════════════════════════════════════════════════════════════
    # Halaman Terakhir — Kesimpulan & Rekomendasi
    # ══════════════════════════════════════════════════════════════════════════
    cur[0] = _running_hdr(c, nama_dosen)
    y = cur[0]

    final_badge = chr(ord("B") + len(detail_matkul))
    y = _sec_hdr(c, final_badge, "KESIMPULAN & REKOMENDASI", y)
    y -= 16

    # Split rekomendasi jadi 2 paragraf (paragraf 1 = kesimpulan, paragraf 2 = rekomendasi)
    rek_paragraphs = [p.strip() for p in (rekomendasi or "").split("\n\n") if p.strip()]

    if len(rek_paragraphs) >= 2:
        para1 = rek_paragraphs[0]
        para2 = "\n\n".join(rek_paragraphs[1:])
    else:
        para1 = rekomendasi or "-"
        para2 = ""

    st_body = _ps(fontSize=9.5, leading=14, alignment=TA_JUSTIFY)

    # Paragraf kesimpulan
    c.setFillColor(C_TXT)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(ML, y, "Kesimpulan")
    y -= 10
    y = _para(c, para1, st_body, ML, y, CW)
    y -= 18

    # Paragraf rekomendasi
    if para2:
        c.setFillColor(C_TXT)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(ML, y, "Rekomendasi")
        y -= 10
        y = _para(c, para2, st_body, ML, y, CW)
        y -= 20

    # Garis tanda tangan
    y -= 20
    sig_w = 160
    c.setStrokeColor(C_BDR)
    c.setLineWidth(0.7)
    c.line(ML, y, ML + sig_w, y)
    y -= 14
    c.setFillColor(C_LBL)
    c.setFont("Helvetica", 8)
    c.drawString(ML, y, "Diketahui oleh,")

    _footer(c, pg[0], total_pages)
    c.save()

    logger.info(f"[EVAL PDF] Saved → {output_path} ({pg[0]} halaman)")
    return output_path


# ══════════════════════════════════════════════════════════════════════════════
# Helper: estimasi tinggi paragraf (untuk pre-check sebelum draw)
# ══════════════════════════════════════════════════════════════════════════════

def _estimate_para_height(text: str, width: float, font_size: float, leading: float) -> float:
    """Estimasi kasar tinggi paragraf berdasarkan karakter per baris."""
    chars_per_line = max(1, int(width / (font_size * 0.52)))
    num_lines      = max(1, len(text) // chars_per_line + 1)
    return num_lines * leading
