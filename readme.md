
# `FITBOT - Your Personal Fitness Assistant`

**Description**  
FitBot adalah asisten fitness personal berbasis artificial intelligence yang dirancang untuk menjadi teman andalanmu. Mendukung misi SDGs "AI for Good Health and Well-being," FitBot fokus pada sisi kebugaran melalui fitness dengan menyediakan program latihan yang disesuaikan, tips nutrisi berbasis sains, dan semua jawaban dari pertanyaan kamu. FitBot siap membantumu berlatih lebih cerdas dan mencapai targetmu lebih cepat.

**Theme** 
AI for Good Health and Well-being

## 🧑‍💻 Team

| **Name**                   | **Role**               |
|--------------------------- |------------------------|
|                            |                        |
|                            |                        |
|                            |                        |
|                            |                        |


---

## 🚀 Features
- **🤖 Asisten Fitness Pribadimu**: Dapatkan jawaban instan dan berbasis ilmiah untuk semua pertanyaanmu seputar program latihan, nutrisi, hingga pemulihan (recovery), didukung oleh kecerdasan buatan Google Gemini.
- **🥗 Saran Nutrisi Cerdas**: Memberikan rekomendasi pola makan sehat yang disesuaikan dengan profil pengguna, seperti kebutuhan kalori harian dan preferensi diet.
- **📊 Tingkat Kesulitan Latihan yang Adaptif**: Menyesuaikan intensitas latihan secara otomatis berdasarkan progres dan feedback pengguna, sehingga program tetap menantang namun aman.
- **🎯 Pencocokan Program Latihan Cerdas**: Menawarkan latihan alternatif serupa jika peralatan tertentu tidak tersedia atau jika pengguna memiliki batasan fisik.
- **🌐 Aksesibilitas UI**: Antarmuka yang mudah diakses (accessible design), mendukung navigasi keyboard dan screen reader, serta memiliki kontras warna yang optimal.
- **📆 Buat jadwal latihan di google kalender**: Memudahkan pengguna untuk menjadwalkan latihan fitness dengan integrasi langsung dengan google calendar
- **⚙️ Pencarian lebih faktual dan kredibel**: Dengan implementasi Retrieval-augmented generation chatbot memiliki kemampuan mempelajari jurnal-jurnal pilihan untuk memberikan jawaban yang berdasarkan fakta



## 🛠 Tech Stack

**Frontend:**
- Bahasa Pemrograman : Typescript
- Framework : Next.js dengan react
- Styling : Tailwind CSS
- Markdown Renderer : React Markdown

**Backend:**
- Bahasa Pemrograman : Python
- Framework : FastAPI
- API : Google AI Gemini
- Validasi Data : Pydantic

---

## 🚀 How to Run the Project

### Step 1. Clone the Repository
```bash
git clone https://github.com/fazarirazkaa/HolyFit.git
```


### Step 2 Run API Backend pada Python di Terminal Baru
```bash
cd Fitbot-AI-Chatbot
cd google
pip install -r requirements.txt
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Step 3 Run Frontend di Terminal Baru
```bash
cd Fitbot-AI-Chatbot
cd fe
npm install
npm run dev
```

### Step 4 Buka localhost:3000 di browser

### Login Authentikasi Google 
```

## 📋 Requirements (optional)
- Node.js versi 18.18 atau lebih baru.
- Python versi 3.10 atau lebih baru.