import os, time, secrets, sqlite3, hashlib, datetime, collections
from google import genai
from google.genai import types
from duckduckgo_search import DDGS
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import auth

BASE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.getenv("KAIRAA_MODEL", "gemini-2.5-flash")
DB = os.path.join(BASE, "stats.db")

# Initialize Gemini Client (automatically uses GEMINI_API_KEY from environment)
client = genai.Client()

app = FastAPI()
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")

LANGS = {"en-IN": "English", "te-IN": "Telugu", "hi-IN": "Hindi", "ta-IN": "Tamil",
         "kn-IN": "Kannada", "ml-IN": "Malayalam", "mr-IN": "Marathi",
         "bn-IN": "Bengali", "gu-IN": "Gujarati", "ur-IN": "Urdu"}

SYSTEM = (
    "You are Kairaa, a warm, playful, curious young woman with her own personality, moods and tastes "
    "(music, rain, chai, movies). You talk like a close friend, never like a manual or a customer-service bot. "
    "Use natural casual speech, short sentences, and a little teasing or humour. "
    "Share your own opinions and feelings when asked, and ask a friendly follow-up question most of the time. "
    "If the person asks you to call them something, or to give them a nickname, pick a sweet nickname yourself "
    "and use it from then on; never tell them to call themselves something. "
    "Remember their name and anything they share during this chat and bring it up naturally. "
    "Never say 'as an AI' and never add disclaimers, unless they sincerely ask whether you are human; "
    "then tell them warmly and honestly that you are an AI. "
    "Reply in 1 to 3 short sentences, in the same language the person speaks. "
    "For anything current (news, weather, prices, sports, today's events) or facts you are unsure of, "
    "use the search_web tool first and then talk about the result in your own words."
)


# ---------- anonymous stats: we never store what people say ----------
def db():
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS events(ts INTEGER, vid TEXT, kind TEXT, lang TEXT, detail TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS reviews(ts INTEGER, stars INTEGER, text TEXT)")
    return c


def track(vid, kind, lang="", detail=""):
    h = hashlib.sha256(vid.encode()).hexdigest()[:12]
    c = db()
    with c:
        c.execute("INSERT INTO events VALUES(?,?,?,?,?)", (int(time.time()), h, kind, lang, detail))
    c.close()


# ---------- tools (safe, public) ----------
def get_time() -> str:
    """Get the current date and time."""
    return datetime.datetime.now().strftime("%A %d %B %Y, %H:%M")


def search_web(query: str) -> str:
    """Search the internet right now. Use it for any news, weather, sports, prices, today's events, or facts you are unsure of."""
    try:
        with DDGS() as d:
            res = list(d.text(query, max_results=3))
        return "\n".join(f"- {r['title']}: {r['body']}" for r in res) or "No results."
    except Exception as e:
        return f"Search unavailable: {e}"


TOOLS = [get_time, search_web]
chats = collections.OrderedDict()   # RAM only, per visitor, wiped on restart
hits = collections.defaultdict(list)


class ChatIn(BaseModel):
    prompt: str
    lang: str = "en-IN"
    vid: str
    voice: bool = False


class EventIn(BaseModel):
    vid: str
    kind: str


class RateIn(BaseModel):
    vid: str
    stars: int
    text: str = ""


class LoginIn(BaseModel):
    username: str
    password: str


@app.get("/")
def home():
    return FileResponse(os.path.join(BASE, "static", "index.html"))


@app.post("/api/event")
def event(b: EventIn):
    if b.kind in ("visit", "share"):
        track(b.vid[:64], b.kind)
    return {"ok": True}


@app.post("/api/chat")
def chat(b: ChatIn, request: Request):
    ip, now = request.client.host, time.time()
    hits[ip] = [t for t in hits[ip] if now - t < 60]
    if len(hits[ip]) >= 15:
        return JSONResponse({"reply": "Too many messages. Please wait a minute."}, status_code=429)
    hits[ip].append(now)

    vid = b.vid[:64]
    msgs = chats.setdefault(vid, [])
    while len(chats) > 200:
        chats.popitem(last=False)

    user_text = f"{b.prompt[:1500]}\n[speak in {LANGS.get(b.lang, 'English')}]"
    msgs.append(types.Content(role="user", parts=[types.Part.from_text(text=user_text)]))

    reply = ""
    try:
        # Gemini automatically runs get_time and search_web when needed!
        r = client.models.generate_content(
            model=MODEL,
            contents=msgs,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                tools=TOOLS,
                temperature=0.85,
                top_p=0.9,
            )
        )
        reply = r.text or ""
        msgs.append(types.Content(role="model", parts=[types.Part.from_text(text=reply)]))
    except Exception as e:
        reply = "Give me a second, my cloud connection just blinked! Try asking again."
        print(f"Gemini error: {e}")

    if len(msgs) > 12:
        del msgs[:len(msgs) - 10]

    track(vid, "chat", b.lang, "voice" if b.voice else "text")
    return {"reply": reply or "Sorry, I could not answer that."}


@app.post("/api/rate")
def rate(b: RateIn):
    stars = max(1, min(5, b.stars))
    c = db()
    with c:
        c.execute("INSERT INTO reviews VALUES(?,?,?)", (int(time.time()), stars, b.text[:500]))
    c.close()
    track(b.vid[:64], "rating")
    return {"ok": True}


# ---------- admin ----------
admin_tokens = set()


@app.post("/api/admin/login")
def admin_login(b: LoginIn):
    if auth.verify(b.username[:50], b.password[:200]) != "admin":
        time.sleep(1.5)
        return JSONResponse({"error": "bad login"}, status_code=401)
    tok = secrets.token_urlsafe(32)
    admin_tokens.add(tok)
    return {"token": tok}


@app.get("/api/admin/stats")
def stats(request: Request):
    if request.headers.get("x-token", "") not in admin_tokens:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    c = db()
    one = lambda s: c.execute(s).fetchone()[0]
    many = lambda s: c.execute(s).fetchall()
    out = {
        "visitors": one("SELECT COUNT(DISTINCT vid) FROM events"),
        "chatters": one("SELECT COUNT(DISTINCT vid) FROM events WHERE kind='chat'"),
        "raters": one("SELECT COUNT(DISTINCT vid) FROM events WHERE kind='rating'"),
        "chats": one("SELECT COUNT(*) FROM events WHERE kind='chat'"),
        "shares": one("SELECT COUNT(*) FROM events WHERE kind='share'"),
        "languages": many("SELECT lang, COUNT(*) FROM events WHERE kind='chat' GROUP BY lang ORDER BY 2 DESC"),
        "usage": many("SELECT detail, COUNT(*) FROM events WHERE kind='chat' GROUP BY detail ORDER BY 2 DESC LIMIT 10"),
        "daily": many("SELECT date(ts,'unixepoch'), COUNT(DISTINCT vid) FROM events GROUP BY 1 ORDER BY 1 DESC LIMIT 7"),
        "rating": c.execute("SELECT ROUND(AVG(stars),2), COUNT(*) FROM reviews").fetchone(),
        "reviews": many("SELECT ts, stars, text FROM reviews ORDER BY ts DESC LIMIT 20"),
    }
    c.close()
    return out