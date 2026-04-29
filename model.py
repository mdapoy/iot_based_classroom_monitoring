from google import genai
import os
from dotenv import load_dotenv

# load env dulu
load_dotenv()

# ambil API key
api_key = os.getenv("GEMINI_API_KEY")

# validasi biar gak silent error
if not api_key:
    raise ValueError("GEMINI_API_KEY tidak ditemukan di .env")

client = genai.Client(api_key=api_key)


def count_tokens(text: str):
    response = client.models.count_tokens(
        model="gemini-2.5-flash",
        contents=text
    )
    return response.total_tokens


# contoh penggunaan
text = """Dunia kita, termasuk setiap helai rambut, tersusun dari partikel-partikel super kecil bernama atom. Hingga awal tahun 1900-an, fisika klasik berhasil menjelaskan banyak hal, mulai dari gerakan bola hingga orbit planet, dengan asumsi bahwa segala sesuatu bisa diprediksi secara pasti. Namun, keyakinan ini runtuh ketika fisika klasik gagal menjelaskan fenomena di tingkat sub-atomik, seperti radiasi benda hitam dan struktur atom.\n\nTitik balik datang dari Max Planck yang, meski awalnya ragu, mengusulkan bahwa energi tidaklah kontinu melainkan datang dalam paket-paket diskrit yang disebut \"kuanta\", yang berhasil menjelaskan radiasi benda hitam. Penemuan ini meruntuhkan pemahaman ratusan tahun tentang energi. Krisis berlanjut dengan teka-teki perilaku elektron yang aneh—mereka bisa \"melompat\" antar tingkat energi tanpa melewati ruang di antaranya dan tidak memiliki posisi pasti, malah dijelaskan sebagai \"gelombang kemungkinan\".\n\nInilah gerbang menuju Dunia Kuantum, di mana logika sehari-hari tidak berlaku. Partikel dapat mengalami superposisi, yaitu berada dalam berbagai keadaan sekaligus, hingga pengamatan manusia \"memaksa\" mereka memilih satu kondisi secara acak. Ada pula prinsip ketidakpastian Heisenberg, yang menyatakan kita tidak bisa mengetahui posisi dan momentum partikel secara akurat secara bersamaan. Fenomena lain termasuk komplementaritas, di mana partikel bisa berperilaku sebagai gelombang atau partikel tetapi tidak keduanya secara bersamaan; terowongan kuantum, di mana partikel dapat menembus penghalang tanpa energi yang cukup; dan keterikatan kuantum, di mana dua partikel yang terhubung akan saling memengaruhi seketika meskipun terpisah jauh, sebuah konsep yang bahkan membuat Einstein kebingungan. Revolusi kuantum yang lahir dari keraguan ini telah mengubah pemahaman kita tentang alam semesta dan menjadi dasar bagi banyak teknologi modern, menunjukkan bahwa alam semesta jauh lebih aneh dari yang bisa kita bayangkan."""

tokens = count_tokens(text)
print("Total token:", tokens)