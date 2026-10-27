import os, secrets, subprocess, datetime, uuid, ctypes, time, re, pathlib
import urllib.request, urllib.parse, webbrowser
import psutil, pyautogui, ollama
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel
from ddgs import DDGS
import auth

load_dotenv()
app = FastAPI()
MODEL = os.getenv("KAIRAA_MODEL", "qwen2.5:3b")  # must support tool calling


def log(line: str):
    with open("actions.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {line}\n")


# ---------------- TOOLS ----------------
APPS = {"chrome": "start chrome", "notepad": "notepad.exe", "vscode": "code",
        "calculator": "calc.exe", "explorer": "explorer.exe"}


def get_time() -> str:
    """Get the current date and time."""
    return datetime.datetime.now().strftime("%A %d %B %Y, %H:%M")


def system_status() -> str:
    """Get battery, CPU and RAM status of the laptop."""
    b = psutil.sensors_battery()
    return (f"Battery {b.percent if b else 'plugged in'}%, "
            f"CPU {psutil.cpu_percent(interval=0.3)}%, RAM {psutil.virtual_memory().percent}%")


def search_web(query: str) -> str:
    """Search the web for live information such as news, weather, prices, facts."""
    with DDGS() as d:
        res = list(d.text(query, max_results=3))
    return "\n".join(f"- {r['title']}: {r['body']} ({r['href']})" for r in res) or "No results."


def media_play_pause() -> str:
    """Play or pause the current media."""
    pyautogui.press("playpause")
    return "Media toggled."


def volume(direction: str) -> str:
    """Change the volume. direction must be 'up' or 'down'."""
    key = "volumeup" if direction == "up" else "volumedown"
    for _ in range(5):
        pyautogui.press(key)
    return f"Volume {direction}."


def open_app(name: str) -> str:
    """Open an app. Allowed: chrome, notepad, vscode, calculator, explorer."""
    cmd = APPS.get(name.lower())
    if not cmd:
        return "That app is not in the allowed list."
    subprocess.Popen(cmd, shell=True)
    return f"Opened {name}."


def open_website(url: str) -> str:
    """Open a website in the browser on the laptop."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return f"Opened {url}"


def lock_laptop() -> str:
    """Lock the laptop screen."""
    ctypes.windll.user32.LockWorkStation()
    return "Laptop locked."


def close_app(process_name: str) -> str:
    """Close a running app by process name, for example chrome.exe."""
    n = 0
    for p in psutil.process_iter(["name"]):
        if p.info["name"] and p.info["name"].lower() == process_name.lower():
            p.terminate()
            n += 1
    return f"Closed {n} process(es)."


def run_command(command: str) -> str:
    """Run a Windows command line command and return its output."""
    r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
    return (r.stdout or r.stderr or "Done.")[:1500]


def download_file(url: str, filename: str = "") -> str:
    """Download a file from a direct web link (http or https) into the Downloads folder."""
    if not url.startswith(("http://", "https://")):
        return "Only http/https links are allowed."
    name = filename or os.path.basename(urllib.parse.urlparse(url).path) or "download.bin"
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name)
    dest = pathlib.Path.home() / "Downloads" / name
    req = urllib.request.Request(url, headers={"User-Agent": "Kairaa/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
            size = 0
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > 300 * 1024 * 1024:
                    raise ValueError("File is larger than 300 MB")
                f.write(chunk)
    except Exception:
        if dest.exists():
            dest.unlink()
        raise
    return f"Saved to {dest}"


GUEST_TOOLS = [get_time, search_web]
ADMIN_SAFE = [get_time, system_status, search_web, media_play_pause, volume,
              open_app, open_website, lock_laptop]
ADMIN_RISKY = [close_app, run_command, download_file]  # always need your Allow tap
ALL = {f.__name__: f for f in ADMIN_SAFE + ADMIN_RISKY}


def tools_for(role):
    return ADMIN_SAFE + ADMIN_RISKY if role == "admin" else GUEST_TOOLS


def safe_run(fn, args):
    try:
        out = fn(**args)
    except Exception as e:
        out = f"Error: {e}"
    log(f"{fn.__name__}({args}) -> {str(out)[:120]}")
    return out


# ---------------- LANGUAGES ----------------
LANGS = {"en-IN": "English", "te-IN": "Telugu", "hi-IN": "Hindi", "ta-IN": "Tamil",
         "kn-IN": "Kannada", "ml-IN": "Malayalam", "mr-IN": "Marathi",
         "bn-IN": "Bengali", "gu-IN": "Gujarati", "ur-IN": "Urdu"}

# ---------------- SESSIONS (kept in memory only, wiped on restart) ----------------
sessions = {}


def get_session(req: Request):
    return sessions.get(req.headers.get("x-token", ""))


# ---------------- BRAIN ----------------
SYSTEM = {
    "admin": "You are Kairaa, a sharp, confident, human-sounding Jarvis-style assistant running on the owner's "
             "Windows laptop. Use tools to act instead of just talking. Safe actions run immediately; risky ones "
             "are approved by the owner on screen, so just call the tool. Always reply in the language the user "
             "asked in. Keep replies short.",
    "guest": "You are Kairaa, a warm, witty assistant chatting with a guest. You can only chat, tell the time "
             "and search the web. Always reply in the language the user asked in. Keep replies short.",
}
memories = {}   # per user, RAM only
pending = {}


def mem(user, role):
    if user not in memories:
        memories[user] = [{"role": "system", "content": SYSTEM[role]}]
    return memories[user]


def run_calls(user, role, calls):
    msgs = mem(user, role)
    while calls:
        c = calls.pop(0)
        name, args = c.function.name, dict(c.function.arguments)
        fn = ALL.get(name)
        if fn is None or fn not in tools_for(role):
            out = "Tool not allowed."
        elif fn in ADMIN_RISKY:
            pid = uuid.uuid4().hex[:8]
            pending[pid] = {"user": user, "name": name, "args": args, "rest": calls}
            return {"id": pid, "text": f"{name}({args})"}
        else:
            out = safe_run(fn, args)
        msgs.append({"role": "tool", "tool_name": name, "content": str(out)})
    return None


def think(user, role):
    msgs = mem(user, role)
    if len(msgs) > 40:
        del msgs[1:len(msgs) - 30]
    for _ in range(6):
        r = ollama.chat(model=MODEL, messages=msgs, tools=tools_for(role))
        msgs.append(r.message)
        if not r.message.tool_calls:
            return {"reply": r.message.content}
        ap = run_calls(user, role, list(r.message.tool_calls))
        if ap:
            return {"reply": "I need your approval for this.", "approval": ap}
    return {"reply": "Done."}


class LoginIn(BaseModel):
    username: str
    password: str


class ChatIn(BaseModel):
    prompt: str
    lang: str = "en-IN"


class ApproveIn(BaseModel):
    id: str
    allow: bool


@app.post("/login")
def login(body: LoginIn):
    role = auth.verify(body.username[:50], body.password[:200])
    if not role:
        time.sleep(1.5)
        return JSONResponse({"error": "bad login"}, status_code=401)
    tok = secrets.token_urlsafe(32)
    user = body.username.lower()
    sessions[tok] = {"user": user, "role": role}
    return {"token": tok, "user": user, "role": role}


@app.post("/logout")
def logout(request: Request):
    sessions.pop(request.headers.get("x-token", ""), None)
    return {"ok": True}


@app.post("/chat")
def chat(body: ChatIn, request: Request):
    s = get_session(request)
    if not s:
        return JSONResponse({"reply": "Please sign in."}, status_code=401)
    language = LANGS.get(body.lang, "English")
    text = f"(Reply in {language}) {body.prompt[:2000]}"
    mem(s["user"], s["role"]).append({"role": "user", "content": text})
    return think(s["user"], s["role"])


@app.post("/approve")
def approve(body: ApproveIn, request: Request):
    s = get_session(request)
    if not s:
        return JSONResponse({"reply": "Please sign in."}, status_code=401)
    p = pending.pop(body.id, None)
    if s["role"] != "admin" or not p or p["user"] != s["user"]:
        return JSONResponse({"reply": "Invalid or expired request."}, status_code=400)
    if body.allow:
        out = safe_run(ALL[p["name"]], p["args"])
    else:
        out = "User denied permission."
        log(f"DENIED {p['name']}({p['args']})")
    mem(s["user"], s["role"]).append({"role": "tool", "tool_name": p["name"], "content": str(out)})
    ap = run_calls(s["user"], s["role"], p["rest"])
    if ap:
        return {"reply": "Another action needs approval.", "approval": ap}
    return think(s["user"], s["role"])


# ---------------- LOGO + UI ----------------
LOGO = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="__SIZE__" height="__SIZE__">'
    '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#38bdf8"/><stop offset="1" stop-color="#6366f1"/></linearGradient></defs>'
    '<circle cx="50" cy="50" r="46" fill="#0b1220" stroke="url(#g)" stroke-width="4"/>'
    '<circle cx="50" cy="50" r="35" fill="none" stroke="#38bdf8" stroke-width="1.5" '
    'stroke-dasharray="6 5" opacity=".7"/>'
    '<text x="50" y="65" text-anchor="middle" font-family="Arial,sans-serif" font-weight="700" '
    'font-size="44" fill="url(#g)">K</text></svg>'
)

PAGE = r"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<meta name="theme-color" content="#07090e">
<link rel="icon" href="/logo.svg">
<link rel="manifest" href="/manifest.json">
<title>Kairaa</title>
<style>
*{box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:#07090e;color:#f1f5f9;margin:0;height:100dvh}
input,select,button{padding:12px 14px;border-radius:10px;border:1px solid #334155;background:#1e293b;color:#fff;font-size:15px;outline:none}
#login{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:24px}
#login h1{margin:4px 0 0;letter-spacing:4px;color:#38bdf8}
#login p{margin:0 0 8px;color:#94a3b8;font-size:14px}
#login input,#login button{width:100%;max-width:320px}
.primary{background:#0284c7;border:none;font-weight:600}
#err{color:#f87171;min-height:18px;font-size:14px}
#app{display:none;flex-direction:column;height:100%}
header{background:#0f172a;padding:10px 14px;border-bottom:1px solid #1e293b;display:flex;align-items:center;gap:10px}
.brand{font-size:17px;font-weight:700;color:#38bdf8;letter-spacing:2px;flex:1}
header select,header button{padding:8px 10px;font-size:13px}
#chat{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:12px}
.bubble{max-width:84%;padding:12px 16px;border-radius:16px;line-height:1.45;word-wrap:break-word;white-space:pre-wrap}
.user{align-self:flex-end;background:#0284c7}
.kairaa{align-self:flex-start;background:#1e293b;border:1px solid #475569}
footer{padding:12px;background:#0f172a;display:flex;gap:8px;border-top:1px solid #1e293b}
footer input{flex:1;min-width:0}
.mic{background:#e11d48;border:none}
.ok{background:#16a34a;border:none;margin:8px 8px 0 0}.no{background:#dc2626;border:none}
</style>
</head>
<body>
<div id="login">
  __LOGO_BIG__
  <h1>KAIRAA</h1>
  <p>Your personal AI assistant</p>
  <input id="u" placeholder="Username" autocapitalize="none" autocomplete="username">
  <input id="p" type="password" placeholder="Password" autocomplete="current-password" onkeypress="if(event.key==='Enter') doLogin()">
  <button class="primary" onclick="doLogin()">Sign in</button>
  <div id="err"></div>
</div>

<div id="app">
  <header>
    __LOGO_SMALL__
    <div class="brand">KAIRAA</div>
    <select id="lang">
      <option value="en-IN">English</option><option value="te-IN">తెలుగు</option>
      <option value="hi-IN">हिन्दी</option><option value="ta-IN">தமிழ்</option>
      <option value="kn-IN">ಕನ್ನಡ</option><option value="ml-IN">മലയാളം</option>
      <option value="mr-IN">मराठी</option><option value="bn-IN">বাংলা</option>
      <option value="gu-IN">ગુજરાતી</option><option value="ur-IN">اردو</option>
    </select>
    <button onclick="doLogout()">Exit</button>
  </header>
  <div id="chat"></div>
  <footer>
    <button class="mic" onclick="listenVoice()">Mic</button>
    <input id="msg" placeholder="Talk to Kairaa..." onkeypress="if(event.key==='Enter') send()">
    <button class="primary" onclick="send()">Send</button>
  </footer>
</div>

<script>
const $ = id => document.getElementById(id);
let token = localStorage.getItem('kt') || '';
let who = localStorage.getItem('ku') || '';
let role = localStorage.getItem('kr') || '';
if (token) enter();

function enter(){
  $('login').style.display='none'; $('app').style.display='flex';
  if(!$('chat').children.length) addBubble('Online. Hello '+who+'. What are we doing?','kairaa');
}
function leave(){
  token=''; localStorage.removeItem('kt'); $('chat').innerHTML='';
  $('app').style.display='none'; $('login').style.display='flex';
}
async function doLogin(){
  const r = await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({username:$('u').value.trim(),password:$('p').value})});
  if(!r.ok){ $('err').innerText='Wrong username or password'; return; }
  const d = await r.json();
  token=d.token; who=d.user; role=d.role;
  localStorage.setItem('kt',token); localStorage.setItem('ku',who); localStorage.setItem('kr',role);
  $('p').value=''; $('err').innerText=''; enter();
}
async function doLogout(){
  try{ await fetch('/logout',{method:'POST',headers:{'x-token':token}}); }catch(e){}
  leave();
}
async function api(path, body){
  const res = await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','x-token':token},body:JSON.stringify(body)});
  if(res.status===401){ leave(); return {reply:''}; }
  return await res.json();
}
function addBubble(text,type){
  const d=document.createElement('div'); d.className='bubble '+type; d.innerText=text;
  const c=$('chat'); c.appendChild(d); c.scrollTop=c.scrollHeight; return d;
}
function show(data){
  if(!data.reply && !data.approval) return;
  addBubble(data.reply,'kairaa');
  if(data.approval){
    const d=addBubble('Allow this action?\n'+data.approval.text,'kairaa');
    const y=document.createElement('button'); y.className='ok'; y.innerText='Allow';
    const n=document.createElement('button'); n.className='no'; n.innerText='Deny';
    const answer=async(allow)=>{ y.disabled=n.disabled=true; show(await api('/approve',{id:data.approval.id,allow:allow})); };
    y.onclick=()=>answer(true); n.onclick=()=>answer(false);
    d.appendChild(document.createElement('br')); d.appendChild(y); d.appendChild(n);
  } else { speakOut(data.reply); }
}
async function send(){
  const i=$('msg'); const t=i.value.trim(); if(!t) return;
  addBubble(t,'user'); i.value='';
  show(await api('/chat',{prompt:t,lang:$('lang').value}));
}
function speakOut(t){
  if('speechSynthesis' in window){
    speechSynthesis.cancel();
    const u=new SpeechSynthesisUtterance(t); u.lang=$('lang').value; speechSynthesis.speak(u);
  }
}
function listenVoice(){
  const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
  if(!SR) return alert('Voice needs Chrome and a secure (https) link.');
  const r=new SR(); r.lang=$('lang').value;
  r.onresult=e=>{ $('msg').value=e.results[0][0].transcript; send(); };
  r.onerror=e=>alert('Voice error: '+e.error);
  r.start();
}
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def home():
    page = PAGE.replace("__LOGO_BIG__", LOGO.replace("__SIZE__", "96"))
    page = page.replace("__LOGO_SMALL__", LOGO.replace("__SIZE__", "34"))
    return page


@app.get("/logo.svg")
def logo():
    return Response(LOGO.replace("__SIZE__", "512"), media_type="image/svg+xml")


@app.get("/manifest.json")
def manifest():
    return JSONResponse({
        "name": "Kairaa", "short_name": "Kairaa", "start_url": "/", "display": "standalone",
        "background_color": "#07090e", "theme_color": "#07090e",
        "icons": [{"src": "/logo.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}],
    })