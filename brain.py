import ollama, subprocess, datetime, webbrowser, psutil

APPS = {"notepad": "notepad.exe", "calculator": "calc.exe",
        "chrome": "chrome.exe", "explorer": "explorer.exe"}

def get_time() -> str:
    """Get current date and time."""
    return datetime.datetime.now().strftime("%A %d %B %Y, %H:%M")

def system_status() -> str:
    """Get CPU, RAM and battery status."""
    b = psutil.sensors_battery()
    return f"CPU {psutil.cpu_percent()}%, RAM {psutil.virtual_memory().percent}%, battery {b.percent if b else 'n/a'}%"

def open_app(name: str) -> str:
    """Open an app: notepad, calculator, chrome, explorer."""
    if name.lower() not in APPS: return "App not in allowed list"
    subprocess.Popen(APPS[name.lower()]); return f"Opened {name}"

def open_website(url: str) -> str:
    """Open a website in the browser."""
    webbrowser.open(url); return f"Opened {url}"

def close_app(process_name: str) -> str:
    """Close a running app by process name, e.g. chrome.exe."""
    n = 0
    for p in psutil.process_iter(["name"]):
        if p.info["name"] and p.info["name"].lower() == process_name.lower():
            p.terminate(); n += 1
    return f"Closed {n} processes"

def run_command(command: str) -> str:
    """Run a Windows shell command."""
    r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
    return (r.stdout or r.stderr)[:1500]

SAFE = [get_time, system_status, open_app, open_website]
RISKY = [close_app, run_command]
TOOLS = {f.__name__: f for f in SAFE + RISKY}

def execute(name, args):
    if name not in TOOLS: return "Unknown tool"
    if TOOLS[name] in RISKY:
        if input(f"\nâš  Kairaa wants to run {name}({args}). Allow? (y/n): ").lower() != "y":
            return "User denied permission"
    return TOOLS[name](**args)

messages = [{"role": "system", "content":
    "You are Kairaa, a proactive Jarvis-style assistant on the user's Windows laptop. "
    "Use tools to act instead of explaining. Be brief."}]

while True:
    messages.append({"role": "user", "content": input("\nYou: ")})
    while True:
        r = ollama.chat(model="qwen2.5:3b", messages=messages, tools=SAFE + RISKY)
        messages.append(r.message)
        if not r.message.tool_calls: break
        for c in r.message.tool_calls:
            out = execute(c.function.name, c.function.arguments)
            messages.append({"role": "tool", "tool_name": c.function.name, "content": str(out)})
    print("Kairaa:", r.message.content)
