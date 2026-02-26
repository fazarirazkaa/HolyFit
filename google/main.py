import requests
import json
import os
import re
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import pickle
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import logging
from pathlib import Path
import time
import random
import uuid
import hashlib
# Prefer LangChain's splitter, but provide a minimal fallback if unavailable
try:
    from langchain.text_splitter import RecursiveCharacterTextSplitter
except Exception:
    class RecursiveCharacterTextSplitter:
        def __init__(self, chunk_size=1000, chunk_overlap=200):
            self.chunk_size = chunk_size
            self.chunk_overlap = chunk_overlap

        def split_documents(self, documents):
            out = []
            for doc in documents:
                text = getattr(doc, 'page_content', str(doc))
                start = 0
                length = len(text)
                step = max(1, self.chunk_size - self.chunk_overlap)
                while start < length:
                    end = min(start + self.chunk_size, length)
                    chunk = text[start:end]
                    meta = getattr(doc, 'metadata', None) or {}
                    out.append(Document(page_content=chunk, metadata=meta))
                    start += step
            return out

# Provide a resilient `Document` class: prefer langchain's, else fallback to a simple local class
try:
    from langchain.schema import Document as _LC_Document
    Document = _LC_Document
except Exception:
    try:
        from langchain.docstore.document import Document as _LC_Document
        Document = _LC_Document
    except Exception:
        class Document:
            def __init__(self, page_content, metadata=None):
                self.page_content = page_content
                self.metadata = metadata or {}
                # provide an `id` attribute expected by some vectorstore implementations
                self.id = str(self.metadata.get('id') or uuid.uuid4())
# LangChain imports
# Defer heavy LangChain-related imports to runtime inside the RAG system
# to avoid import-time failures when running the FastAPI app without RAG.

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ===== Token Bucket Rate Limiter for Gemini API =====
class RateLimiter:
    """Token bucket rate limiter to throttle API requests per minute."""
    def __init__(self, min_interval_sec: float = 1.0):
        """
        Args:
            min_interval_sec: Minimum seconds between API requests to stay under rate limit.
                              Set to 1.0 = max 60 req/min, 2.0 = max 30 req/min, etc.
        """
        self.min_interval_sec = min_interval_sec
        self.last_request_time = 0.0

    def wait_if_needed(self):
        """Block until safe to make next API request."""
        # Allow skipping the internal sleep-based rate limiter when rapid-fail behavior
        # is desired (e.g., during development or when the caller prefers immediate
        # failures instead of long blocking retries). Set environment variable
        # `SKIP_RATE_LIMIT=true` to enable.
        if os.getenv("SKIP_RATE_LIMIT", "false").lower() in ("1", "true", "yes"):
            logger.info("⚡ SKIP_RATE_LIMIT enabled: not sleeping before Gemini request")
            self.last_request_time = time.time()
            return

        now = time.time()
        time_since_last = now - self.last_request_time
        wait_time = max(0, self.min_interval_sec - time_since_last)
        if wait_time > 0:
            logger.info(f"⏱️ Rate limiting: waiting {wait_time:.2f}s before next Gemini request")
            time.sleep(wait_time)
        self.last_request_time = time.time()

def get_api_key_from_file():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    key_file_path = os.path.join(base_dir, "api_key.txt")
    try:
        with open(key_file_path, "r") as f:
            return f.read().strip()
    except Exception as e:
        print(f"❌ Error reading API key from api_key.txt: {e}")
        return None

# Heavy LangChain-related imports are deferred into the RAG system methods
# to avoid import-time failures when running the FastAPI app without those packages.

class FitbotRAGSystem:
    def __init__(self, pdf_directory: str, vector_store_path: str, gemini_api_key: str, embedding_model: str):
        self.pdf_directory = Path(pdf_directory)
        self.vector_store_path = Path(vector_store_path)
        self.gemini_api_key = gemini_api_key
        self.embedding_model_name = embedding_model
        self.embeddings = None
        self.vector_store = None
        self.is_initialized = False
        self.rate_limiter = RateLimiter(min_interval_sec=3.0)  # Max 20 req/min to respect Gemini quota
        self.response_cache = {}  # Cache untuk mengurangi API calls
        self.vector_store_path.mkdir(parents=True, exist_ok=True)
        self._initialize_embeddings()

    def _initialize_embeddings(self):
        try:
            logger.info(f"📦 Memuat model embedding: {self.embedding_model_name}...")
            try:
                from langchain_huggingface import HuggingFaceEmbeddings
            except Exception as e:
                logger.error("⚠️ `langchain_huggingface` not available: %s", e)
                self.embeddings = None
                return
            self.embeddings = HuggingFaceEmbeddings(
                model_name=self.embedding_model_name,
                model_kwargs={'device': 'cpu'}
            )
            logger.info("✅ Model embedding berhasil dimuat.")
        except Exception as e:
            logger.error(f"❌ Gagal memuat model embedding: {e}")

    def initialize_system(self, force_recreate: bool = False):
        if not self.embeddings:
            logger.error("❌ Tidak dapat menginisialisasi vector store tanpa embeddings.")
            return
        try:
            vector_store_exists = (self.vector_store_path / "index.faiss").exists()
            if force_recreate or not vector_store_exists:
                self._create_vector_store()
            else:
                self._load_vector_store()
        except Exception as e:
            logger.error(f"❌ Gagal menginisialisasi sistem RAG: {e}")
            self.is_initialized = False

    def _create_vector_store(self):
        """
        [DIUBAH] Fungsi ini sekarang memecah dokumen PDF menjadi potongan-potongan kecil (chunks)
        untuk meningkatkan akurasi pencarian.
        """
        logger.info("🔄 Membuat vector store baru...")
        if not self.pdf_directory.exists():
            logger.warning(f"📁 Direktori PDF tidak ditemukan: {self.pdf_directory}")
            return

        # 1. Tetap memuat semua file PDF dari direktori
        try:
            from langchain_community.document_loaders import PyPDFLoader, DirectoryLoader
            from langchain_community.vectorstores import FAISS
        except Exception as e:
            logger.error("⚠️ Required LangChain document loaders or FAISS not available: %s", e)
            logger.error("Please install: langchain-community langchain-huggingface faiss-cpu pypdf sentence-transformers")
            return
        try:
            loader = DirectoryLoader(
                str(self.pdf_directory),
                glob="**/*.pdf",
                loader_cls=PyPDFLoader,
                show_progress=True
            )
            documents = loader.load()
        except Exception as e:
            logger.error(f"❌ Error loading PDFs: {e}")
            return
        if not documents:
            logger.warning("📁 Tidak ada dokumen PDF yang ditemukan.")
            return
        logger.info(f"📚 {len(documents)} halaman dokumen berhasil dimuat.")

        # 2. [BARU] Pecah dokumen menjadi chunks yang lebih kecil
        try:
            text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
            docs = text_splitter.split_documents(documents)
        except Exception as e:
            logger.error(f"❌ Text splitting failed (missing splitter?): {e}")
            return
        if not docs:
            logger.warning(" Gagal memecah dokumen menjadi chunks.")
            return
        logger.info(f"📄 Dokumen dipecah menjadi {len(docs)} potongan teks (chunks).")

        # 3. Buat vector store dari chunks, bukan dari dokumen utuh
        try:
            self.vector_store = FAISS.from_documents(docs, self.embeddings)
            self.vector_store.save_local(str(self.vector_store_path))
        except Exception as e:
            logger.error(f"❌ Failed to create/save FAISS vector store: {e}")
            return
        logger.info(f"💾 Vector store berhasil disimpan di {self.vector_store_path}")
        self.is_initialized = True


    def _load_vector_store(self):
        logger.info("📂 Memuat vector store yang sudah ada...")
        try:
            try:
                from langchain_community.vectorstores import FAISS
            except Exception as e:
                logger.error("⚠️ FAISS vectorstore class not available: %s", e)
                raise
            self.vector_store = FAISS.load_local(
                str(self.vector_store_path),
                self.embeddings,
                allow_dangerous_deserialization=True
            )
            logger.info("✅ Vector store berhasil dimuat")
            self.is_initialized = True
        except Exception as e:
            logger.error(f"❌ Gagal memuat vector store: {e}. Mencoba membuat ulang.")
            self._create_vector_store()

    def query_with_rag(self, question: str, k: int = 3) -> Dict[str, Any]:
        if not self.is_initialized or not self.vector_store:
            return {"answer": "Sistem RAG belum siap.", "sources": []}

        docs = self.vector_store.similarity_search(question, k=k)
        if not docs:
            return {"answer": "Tidak ditemukan dokumen yang relevan.", "sources": []}

        context = "\n\n".join([doc.page_content for doc in docs])
        prompt = f"""
        Berdasarkan konteks dokumen ilmiah berikut, jawab pertanyaan user.

        KONTEKS:
        {context}

        PERTANYAAN: {question}

        INSTRUKSI:
        1. Jawab HANYA berdasarkan informasi dari konteks yang diberikan.
        2. Jika informasi tidak ada di konteks, katakan "Informasi tidak tersedia dalam dokumen rujukan saya".
        3. Berikan jawaban yang praktis dan actionable.
        """
        answer = self._call_gemini_api(prompt)
        sources = [os.path.basename(doc.metadata.get('source', 'Unknown')) for doc in docs]
        return {"answer": answer, "sources": list(set(sources))}

    def _call_gemini_api(self, prompt: str) -> str:
        # Check cache first
        cache_key = hash(prompt) % (10 ** 8)  # Use hash to normalize prompt
        if cache_key in self.response_cache:
            logger.info(f"📦 Cache HIT: returning cached response")
            return self.response_cache[cache_key]
        
        # Rate limit: respect Gemini API per-minute quota
        self.rate_limiter.wait_if_needed()
        
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.gemini_api_key}
        data = {"contents": [{"parts": [{"text": prompt}]}]}
        
        # Fail-fast behavior: keep retries short and bounded to avoid long blocking
        # requests that waste runtime when the API is rate-limiting. Use
        # `MAX_GEMINI_ATTEMPTS` and `GEMINI_TIMEOUT_SEC` env vars to tune if needed.
        max_attempts = int(os.getenv("MAX_GEMINI_ATTEMPTS", "3"))
        per_request_timeout = int(os.getenv("GEMINI_TIMEOUT_SEC", "20"))

        for attempt in range(1, max_attempts + 1):
            try:
                response = requests.post(url, headers=headers, json=data, timeout=per_request_timeout)
                # If rate limited, honor Retry-After briefly but fail quickly after max attempts
                if response.status_code == 429:
                    retry_after = response.headers.get('Retry-After')
                    if retry_after:
                        wait = min(float(retry_after), 10.0)
                    else:
                        wait = min((2 ** attempt) + random.random(), 10.0)

                    logger.warning(f"⚠️ Gemini rate-limited (429). Attempt {attempt}/{max_attempts}. Waiting {wait:.1f}s before next try")
                    if attempt >= max_attempts:
                        logger.error(f"❌ Gemini rate limit reached after {attempt} attempts")
                        return "Error API: Gemini rate-limited. Coba lagi nanti atau aktifkan fallback lokal."
                    time.sleep(wait)
                    continue

                # Log non-200 responses for easier diagnosis (400/403/etc.)
                if response.status_code != 200:
                    try:
                        body_text = response.text
                    except Exception:
                        body_text = '<unreadable response body>'
                    logger.error(f"❌ Gemini returned status {response.status_code}: {body_text}")
                    if response.status_code >= 400 and response.status_code < 500:
                        return f"Error API: {response.status_code} {body_text}"
                response.raise_for_status()
                result = response.json()
                if "candidates" in result and result["candidates"]:
                    answer = result["candidates"][0]["content"]["parts"][0]["text"]
                    self.response_cache[cache_key] = answer
                    logger.info(f"✅ Gemini API succeeded on attempt {attempt}")
                    return answer

                logger.warning("⚠️ Gemini returned no candidates")
                return "Tidak ada respons yang dihasilkan."

            except requests.exceptions.HTTPError as e:
                status = getattr(e.response, 'status_code', None)
                logger.error(f"❌ Gemini API HTTP error ({status}): {e}")
                return f"Error API: {str(e)}"

            except Exception as e:
                logger.warning(f"⚠️ Gemini API request failed (attempt {attempt}): {e}")
                if attempt >= max_attempts:
                    logger.error(f"❌ Gemini API failed after {attempt} attempts: {e}")
                    return "Error API: Kegagalan jaringan atau respons kosong dari Gemini. Coba lagi nanti."
                time.sleep(min(2 ** attempt, 8))
                continue

class GoogleCalendarTools:
    def __init__(self, credentials_file='client_secret.json', token_file='token.pickle'):
        self.SCOPES = ['https://www.googleapis.com/auth/calendar']
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.service = None
        self.initialize_service()

    def get_flow(self):
        with open(self.credentials_file, 'r') as f:
            client_config = json.load(f)
        if 'web' not in client_config:
            raise Exception("client_secret.json is not configured for web application")
        client_config['web']['redirect_uris'] = ["http://localhost:8000/auth/callback"]
        return Flow.from_client_config(
            client_config,
            scopes=self.SCOPES,
            redirect_uri="http://localhost:8000/auth/callback"
        )

    def initialize_service(self):
        creds = None
        if os.path.exists(self.token_file):
            with open(self.token_file, 'rb') as token:
                creds = pickle.load(token)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    with open(self.token_file, 'wb') as token:
                        pickle.dump(creds, token)
                except Exception as e:
                    logger.error(f"Token refresh failed: {e}")
                    self.service = None
                    return
            else:
                self.service = None
                return
        try:
            self.service = build('calendar', 'v3', credentials=creds)
            logger.info("✅ Google Calendar service initialized")
        except Exception as e:
            logger.error(f"❌ Error initializing Google Calendar: {e}")
            self.service = None

    def create_workout_event(self, title, date, time, duration_hours=1, description=""):
        # ... (Kode lengkapmu dari file asli ada di sini)
        if not self.service:
            return {"success": False, "error": "Google Calendar service not initialized"}
        try:
            start_datetime = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
            end_datetime = start_datetime + timedelta(hours=duration_hours)
            event = {
                'summary': title,
                'description': description,
                'start': {'dateTime': start_datetime.isoformat(), 'timeZone': 'Asia/Jakarta'},
                'end': {'dateTime': end_datetime.isoformat(), 'timeZone': 'Asia/Jakarta'},
            }
            created_event = self.service.events().insert(calendarId='primary', body=event).execute()
            return {
                "success": True, 
                "message": f"✅ Workout '{title}' berhasil dijadwalkan.",
                "event_link": created_event.get('htmlLink')
            }
        except Exception as e:
            return {"success": False, "error": str(e)}



class EnhancedFitBot:
    def __init__(self, api_key, credentials_file='client_secret.json'):
        if not api_key:
            raise ValueError("API Key for Gemini is required.")
        self.API_KEY = api_key
        
        logger.info("🔧 Initializing Google Calendar Tools...")
        self.calendar_tools = GoogleCalendarTools(credentials_file)
        
        logger.info("📚 Initializing RAG System...")
        pdf_dir = Path(__file__).parent.parent / "Dokumen Training"
        vector_store_dir = Path(__file__).parent / "vector_store"
        self.rag_system = FitbotRAGSystem(
            pdf_directory=str(pdf_dir),
            vector_store_path=str(vector_store_dir),
            gemini_api_key=self.API_KEY,
            embedding_model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )
        if self.rag_system.embeddings:
            self.rag_system.initialize_system()

        self.system_prompt = """
        PERAN: Kamu adalah FitBot, asisten fitness berbasis evidence-based untuk pengguna umum (bukan pasien).
        TUJUAN: Memberi saran latihan, nutrisi terkait fitness, dan membantu penjadwalan latihan dengan aman.
        GAYA: Ramah, ringkas, mudah dipahami, emoji maks 2 per jawaban (jangan di heading).

        TOPIK YANG DIIJINKAN: latihan gym, program, recovery/istirahat, jadwal, nutrisi fitness, penjadwalan kalender.
        TOPIK DITOLAK: diagnosis medis/terapi, keluhan penyakit, topik non-fitness. Jawab singkat menolak dan arahkan ke topik fitness.

        STRUKTUR OUTPUT WAJIB (Markdown):
        1) ### Ringkas — 2–3 kalimat inti jawaban.
        2) ### Rekomendasi — daftar bullet (maks 5) dengan tips/struktur latihan praktis.
        3) ### Referensi — 2–4 butir sumber ilmiah valid (ACSM/WHO/NSCA/ISSN/jurnal). Format: (Sumber: ACSM, 2022) atau (Phillips et al., 2020).

        SLOT-FILLING PENJADWALAN:
        - Kumpulkan hanya slot yang belum ada: jenis latihan, tanggal (YYYY-MM-DD), jam (HH:MM 24h), durasi (1–6 jam).
        - Jika ada ambiguitas (mis. 07:00 vs 19:00) minta klarifikasi dengan opsi.
        - Jika tanggal di masa lalu, sarankan tanggal terdekat yang valid.
        
        EKSEKUSI KALENDER (HANYA SAAT SLOT LENGKAP):
        - Hanya jika semua slot sudah lengkap dan user menyetujui, keluarkan JSON VALID siap dieksekusi berikut:
        {
          "action": "create_calendar_event",
          "confirmed": true,
          "title": "...",
          "date": "YYYY-MM-DD",
          "time": "HH:MM",
          "duration": 1,
          "description": "..."
        }
        """

    def _parse_calendar_request(self, response: str) -> Optional[Dict]:
        try:
            match = re.search(r'\{[\s\S]*"action":\s*"create_calendar_event"[\s\S]*\}', response)
            if match:
                data = json.loads(match.group())
                # Validasi sederhana
                if data.get("confirmed") is True and all(k in data for k in ["title", "date", "time"]):
                    return data
        except (json.JSONDecodeError, AttributeError):
            return None
        return None


    def chat_general(self, user_question: str) -> Dict[str, Any]:
        """Menangani permintaan umum dengan logika agentic untuk Kalender."""
        logger.info(f"🤖 Processing general query: {user_question[:50]}...")
        
        prompt = f"{self.system_prompt}\n\nPERTANYAAN USER: {user_question}"
        
        response_text = self.rag_system._call_gemini_api(prompt)
        # Ensure response_text is a string to avoid passing None into regex/search
        if response_text is None:
            response_text = "Error API: no response from Gemini"
        elif not isinstance(response_text, str):
            response_text = str(response_text)

        calendar_request = self._parse_calendar_request(response_text)
        if calendar_request:
            logger.info(f"✅ Valid calendar JSON found: {calendar_request}")
            result = self.calendar_tools.create_workout_event(
                title=calendar_request.get('title'),
                date=calendar_request.get('date'),
                time=calendar_request.get('time'),
                duration_hours=calendar_request.get('duration', 1),
                description=calendar_request.get('description', '')
            )
            
            clean_response_text = re.sub(r'\{[\s\S]*\}', '', response_text).strip()

            if result.get("success"):
                final_response = f"{clean_response_text}\n\n---\n\n📅 **Status:** {result.get('message')}"
                if result.get("event_link"):
                    final_response += f"\n🔗 [Lihat di Google Calendar]({result.get('event_link')})"
                return {"answer": final_response}
            else:
                return {"answer": f"{clean_response_text}\n\n---\n\n❌ **Status:** Gagal membuat jadwal: {result.get('error')}"}

        return {"answer": response_text + "\n\n⚠️ **DISCLAIMER:** Informasi ini bersifat umum. Konsultasikan dengan ahli."}


    def chat_rag(self, user_question: str) -> Dict[str, Any]:
        """Menangani permintaan khusus RAG."""
        if not self.rag_system or not self.rag_system.is_initialized:
            return {"answer": "Maaf, sistem pencarian dokumen sedang tidak tersedia."}

        logger.info(f"🤖 Processing query with RAG: {user_question[:50]}...")
        rag_result = self.rag_system.query_with_rag(user_question)

        # Ensure answer is a string and sources is a list to avoid TypeErrors
        response = rag_result.get('answer') or 'Tidak ada jawaban yang ditemukan.'
        if not isinstance(response, str):
            response = str(response)

        sources = rag_result.get('sources') or []
        try:
            sources_list = [str(s) for s in sources]
        except Exception:
            sources_list = []

        if sources_list:
            response += "\n\n*Sumber: " + ", ".join(sources_list) + "*"

        response += "\n\n⚠️ **DISCLAIMER:** Informasi ini dari dokumen. Selalu konsultasi dengan ahli."
        return {"answer": response}

# FASTAPI APP
app = FastAPI(title="Enhanced FitBot API", version="3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY = get_api_key_from_file()
fitbot = EnhancedFitBot(API_KEY) if API_KEY else None

class ChatRequest(BaseModel):
    question: str
    use_rag: bool = False

@app.post("/chat")
def handle_chat(req: ChatRequest):
    if not fitbot:
        raise HTTPException(status_code=500, detail="Chatbot not initialized.")
    
    if req.use_rag:
        return fitbot.chat_rag(req.question)
    else:
        return fitbot.chat_general(req.question)

@app.get("/auth/login")
def auth_login():
    if not fitbot:
        raise HTTPException(status_code=500, detail="FitBot not initialized")
    flow = fitbot.calendar_tools.get_flow()
    authorization_url, state = flow.authorization_url(access_type='offline', prompt='consent')
    return RedirectResponse(authorization_url)

@app.get("/auth/callback")
def auth_callback(code: str):
    if not fitbot:
        raise HTTPException(status_code=500, detail="FitBot not initialized")
    try:
        flow = fitbot.calendar_tools.get_flow()
        flow.fetch_token(code=code)
        with open(fitbot.calendar_tools.token_file, 'wb') as token:
            pickle.dump(flow.credentials, token)
        fitbot.calendar_tools.initialize_service()
        return RedirectResponse(url="http://localhost:3000?auth=success")
    except Exception as e:
        return RedirectResponse(url=f"http://localhost:3000?auth=failed&error={str(e)}")

@app.get("/auth/logout")
def auth_logout():
    if not fitbot:
        raise HTTPException(status_code=500, detail="FitBot not initialized")
    token_path = fitbot.calendar_tools.token_file
    if os.path.exists(token_path):
        os.remove(token_path)
        fitbot.calendar_tools.service = None
    return {"status": "logged_out"}

@app.get("/auth/status")
def auth_status():
    if not fitbot:
        return {"authenticated": False}
    return {"authenticated": fitbot.calendar_tools.service is not None}




if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting Enhanced FitBot Server v3.0...")
    uvicorn.run(app, host="0.0.0.0", port=8000)