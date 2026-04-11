"""
AI Server Health Agent
======================
Collects metrics from a Dokploy instance + host system (CPU, RAM, disk),
analyzes them with Google Gemini, and sends a rich daily health report to Telegram.

Environment variables are injected at runtime by Dokploy — no .env file is used.

Dokploy API structure (confirmed from source):
  GET /api/project.all → list of projects, each containing:
    environments[] → each environment contains:
      applications[], compose[], postgres[], mysql[], mongo[], mariadb[], redis[]
"""

import os
import time
import subprocess
import requests
from datetime import datetime, timezone
from google import genai

# ---------------------------------------------------------------------------
# Module-level constants — all sourced from Dokploy environment variables
# ---------------------------------------------------------------------------
DOKPLOY_URL = os.environ.get("DOKPLOY_URL")
DOKPLOY_API_KEY = os.environ.get("DOKPLOY_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


# ---------------------------------------------------------------------------
# Helper: collect host system metrics (CPU, RAM, disk)
# ---------------------------------------------------------------------------
def _collect_system_metrics() -> dict:
    """
    Reads CPU usage, RAM usage, and disk usage from the Linux host.
    Uses /proc/stat for CPU, /proc/meminfo for RAM, and the `df` command for disk.

    Returns a dict with keys: cpu_percent, ram_total_gb, ram_used_gb,
    ram_percent, disk_total_gb, disk_used_gb, disk_percent.
    """
    system = {}

    # --- CPU usage (two /proc/stat samples 0.5s apart for accuracy) ---
    try:
        def _read_cpu():
            with open("/proc/stat") as f:
                line = f.readline()
            vals = list(map(int, line.split()[1:]))
            idle = vals[3]
            total = sum(vals)
            return idle, total

        idle1, total1 = _read_cpu()
        time.sleep(0.5)
        idle2, total2 = _read_cpu()
        cpu_percent = round(100 * (1 - (idle2 - idle1) / (total2 - total1)), 1)
        system["cpu_percent"] = cpu_percent
    except Exception as exc:
        system["cpu_percent"] = f"N/A ({exc})"

    # --- RAM usage from /proc/meminfo ---
    try:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    mem[parts[0].rstrip(":")] = int(parts[1])
        total_kb = mem.get("MemTotal", 0)
        avail_kb = mem.get("MemAvailable", 0)
        used_kb = total_kb - avail_kb
        system["ram_total_gb"] = round(total_kb / 1024 / 1024, 2)
        system["ram_used_gb"] = round(used_kb / 1024 / 1024, 2)
        system["ram_percent"] = round(100 * used_kb / total_kb, 1) if total_kb else 0
    except Exception as exc:
        system["ram_total_gb"] = system["ram_used_gb"] = system["ram_percent"] = f"N/A ({exc})"

    # --- Disk usage via df on the root filesystem ---
    try:
        result = subprocess.run(
            ["df", "-BG", "--output=size,used,pcent", "/"],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.strip().splitlines()
        if len(lines) >= 2:
            parts = lines[1].split()
            system["disk_total_gb"] = int(parts[0].replace("G", ""))
            system["disk_used_gb"] = int(parts[1].replace("G", ""))
            system["disk_percent"] = int(parts[2].replace("%", ""))
    except Exception as exc:
        system["disk_total_gb"] = system["disk_used_gb"] = system["disk_percent"] = f"N/A ({exc})"

    return system


# ---------------------------------------------------------------------------
# Function 1: Collect all metrics
# ---------------------------------------------------------------------------
def collect_metrics() -> dict:
    """
    Combines host system metrics (CPU/RAM/disk) with Dokploy service data
    (projects, applications, databases) into a single metrics dictionary.

    Dokploy data is fetched from GET /api/project.all; each project contains
    environments, and each environment contains nested services and databases.

    Returns a dict with:
        - system:       CPU %, RAM (used/total GB + %), disk (used/total GB + %)
        - projects:     list of project names
        - applications: list of dicts with 'name' and 'status'
        - databases:    list of dicts with 'name', 'type', and 'status'
        - timestamp:    ISO-8601 UTC string
    """
    print("Collecting metrics from host system and Dokploy...")

    # --- System metrics ---
    system = _collect_system_metrics()
    print(
        f"  CPU: {system.get('cpu_percent')}%  |  "
        f"RAM: {system.get('ram_used_gb')}GB / {system.get('ram_total_gb')}GB  |  "
        f"Disk: {system.get('disk_used_gb')}GB / {system.get('disk_total_gb')}GB"
    )

    # --- Dokploy data ---
    headers = {"x-api-key": DOKPLOY_API_KEY, "accept": "application/json"}
    base = DOKPLOY_URL.rstrip("/")
    projects, applications, databases = [], [], []

    try:
        response = requests.get(f"{base}/api/project.all", headers=headers, timeout=15)
        response.raise_for_status()
        raw_projects = response.json() if isinstance(response.json(), list) else []
    except requests.RequestException as exc:
        print(f"  WARNING: Failed to call /api/project.all: {exc}")
        raw_projects = []

    for project in raw_projects:
        project_name = project.get("name", "Unknown")
        projects.append(project_name)

        for env in project.get("environments", []):
            # Applications
            for app in env.get("applications", []):
                applications.append({
                    "name": app.get("name", "Unknown"),
                    "status": app.get("applicationStatus", "unknown"),
                })
            # Compose services
            for svc in env.get("compose", []):
                applications.append({
                    "name": svc.get("name", "Unknown") + " (compose)",
                    "status": svc.get("composeStatus", svc.get("applicationStatus", "unknown")),
                })
            # Databases
            db_types = {
                "Postgres": env.get("postgres", []),
                "MySQL":    env.get("mysql", []),
                "MongoDB":  env.get("mongo", []),
                "MariaDB":  env.get("mariadb", []),
                "Redis":    env.get("redis", []),
            }
            for db_type, db_list in db_types.items():
                for db in db_list:
                    databases.append({
                        "name": db.get("name", "Unknown"),
                        "type": db_type,
                        "status": db.get("applicationStatus", "unknown"),
                    })

    print(
        f"  Dokploy: {len(projects)} project(s), "
        f"{len(applications)} app(s), {len(databases)} database(s)."
    )

    return {
        "system": system,
        "projects": projects,
        "applications": applications,
        "databases": databases,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Function 2: Build the Gemini prompt
# ---------------------------------------------------------------------------
def build_prompt(metrics: dict) -> str:
    """
    Formats the collected metrics into a structured prompt for Gemini,
    instructing it to produce a rich, Telegram-ready daily health report
    that covers system resources, app statuses, and database health.

    Args:
        metrics: Dictionary returned by collect_metrics().

    Returns:
        A formatted prompt string ready for the Gemini API.
    """
    print("Building prompt for Gemini...")

    sys = metrics["system"]

    def _fmt_list(items, fn):
        return "\n".join(fn(i) for i in items) if items else "  (none found)"

    prompt = f"""You are a DevOps assistant. Analyze the following server metrics and produce a rich daily health report.

=== REPORT TIMESTAMP ===
{metrics['timestamp']}

=== HOST SYSTEM RESOURCES ===
CPU Usage:   {sys.get('cpu_percent')}%
RAM Usage:   {sys.get('ram_used_gb')} GB used / {sys.get('ram_total_gb')} GB total ({sys.get('ram_percent')}%)
Disk Usage:  {sys.get('disk_used_gb')} GB used / {sys.get('disk_total_gb')} GB total ({sys.get('disk_percent')}%)

=== PROJECTS ===
{_fmt_list(metrics['projects'], lambda p: f'  - {p}')}

=== APPLICATIONS (name: status) ===
{_fmt_list(metrics['applications'], lambda a: f"  - {a['name']}: {a['status']}")}

=== DATABASES (type | name: status) ===
{_fmt_list(metrics['databases'], lambda d: f"  - [{d['type']}] {d['name']}: {d['status']}")}

=== INSTRUCTIONS ===
Write a comprehensive daily server health report for a Telegram message. Include:

1. 📊 SYSTEM HEALTH — summarize CPU, RAM, and Disk. Flag anything above 80% as ⚠️ Warning, above 90% as ❌ Critical.
2. 🚀 APPLICATIONS — list each with ✅ Healthy (status=done/running) or ❌ Issues (error/idle/unknown).
3. 🗄️ DATABASES — list each with ✅ Healthy or ❌ Issues.
4. ⚠️ ANOMALIES — call out anything abnormal (high resource usage, error status, unknown databases, etc.).
5. 💡 RECOMMENDATIONS — give specific, actionable advice for any issues found. If all good, say so clearly.

Format rules:
- Plain text for Telegram (no # headers, no **bold**).
- Use emojis liberally for readability.
- Keep it informative but concise — aim for ~25–35 lines.
- Do NOT include a title line; the caller will add one.
"""
    return prompt


# ---------------------------------------------------------------------------
# Function 3: Analyze with Gemini
# ---------------------------------------------------------------------------
def analyze_with_gemini(prompt: str) -> str:
    """
    Sends the prompt to Google Gemini using the google-genai SDK.
    Tries models in fallback order if a model's quota is exhausted:
      1. gemini-2.5-flash   (best quality)
      2. gemini-2.5-flash-lite  (most quota-friendly)

    Args:
        prompt: The prompt string built by build_prompt().

    Returns:
        The AI-generated report as plain text, or an error message string.
    """
    print("Calling Gemini for analysis...")

    client = genai.Client(api_key=GEMINI_API_KEY)

    # Models in order of preference (April 2026 stable models)
    models = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]

    for model in models:
        try:
            print(f"  Trying model: {model}...")
            response = client.models.generate_content(
                model=model,
                contents=prompt,
            )
            print(f"  Success using {model}.")
            return response.text
        except Exception as exc:
            error_str = str(exc)
            if "429" in error_str or "quota" in error_str.lower():
                print(f"  WARNING: {model} quota exhausted, trying next model...")
                continue
            error_msg = f"⚠️ Gemini analysis failed: {exc}"
            print(f"  ERROR: {error_msg}")
            return error_msg

    error_msg = "⚠️ All Gemini models have exhausted their free-tier quota for today. Try again after midnight UTC."
    print(f"  ERROR: {error_msg}")
    return error_msg


# ---------------------------------------------------------------------------
# Function 4: Send to Telegram
# ---------------------------------------------------------------------------
def send_to_telegram(report: str) -> None:
    """
    Sends the AI-generated report to Telegram via the Bot API.
    Prepends a dated header to the message.

    Common failure — 'chat not found':
      TELEGRAM_CHAT_ID must be a numeric ID (e.g. 123456789 for a user,
      or -1001234567890 for a group/channel). Get yours from @userinfobot.

    Args:
        report: The text report generated by analyze_with_gemini().
    """
    print("Sending report to Telegram...")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    full_message = f"🖥️ Daily Server Report — {today}\n\n{report}"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": full_message}

    try:
        response = requests.post(url, json=payload, timeout=15)
        if not response.ok:
            print(
                f"  ERROR: Telegram {response.status_code}: {response.text}\n"
                f"  HINT: 'chat not found' → check TELEGRAM_CHAT_ID (must be numeric).\n"
                f"  HINT: Get your ID from @userinfobot on Telegram."
            )
        response.raise_for_status()
        print("  Report sent to Telegram successfully.")
    except requests.RequestException as exc:
        print(f"  ERROR: Failed to send Telegram message: {exc}")


# ---------------------------------------------------------------------------
# Main entry point — runs once immediately, then every 24 hours
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=== AI Server Health Agent Started (runs every 24 hours) ===")

    while True:
        print(f"\n--- Running report at {datetime.now(timezone.utc).isoformat()} ---")

        metrics = collect_metrics()
        prompt = build_prompt(metrics)
        report = analyze_with_gemini(prompt)
        send_to_telegram(report)

        print("--- Report done. Sleeping for 24 hours... ---")
        time.sleep(86400)
