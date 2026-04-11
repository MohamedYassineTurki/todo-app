"""
AI Server Health Agent
======================
Collects metrics from a Dokploy instance, analyzes them with Google Gemini,
and sends a daily health report to a Telegram channel.

Environment variables are injected at runtime by Dokploy — no .env file is used.

Dokploy API structure (confirmed from source):
  GET /api/project.all → list of projects, each containing:
    environments[] → each environment contains:
      applications[], compose[], postgres[], mysql[], mongo[], mariadb[], redis[]
"""

import os
import time
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
# Function 1: Collect metrics from the Dokploy REST API
# ---------------------------------------------------------------------------
def collect_metrics() -> dict:
    """
    Calls GET /api/project.all on the Dokploy instance to retrieve all projects
    along with their nested services and databases.

    The Dokploy API nesting structure is:
      project → environments[] → {applications, compose, postgres, mysql,
                                   mongo, mariadb, redis}

    Applications carry an 'applicationStatus' field (idle/running/done/error).
    Databases carry a 'applicationStatus' field as well.

    Returns a dictionary with:
        - projects:     list of project names
        - applications: list of dicts with 'name' and 'status'
        - databases:    list of dicts with 'name', 'type', and 'status'
        - timestamp:    ISO-8601 UTC timestamp of collection time
    """
    print("Collecting metrics from Dokploy...")

    headers = {
        "x-api-key": DOKPLOY_API_KEY,
        "accept": "application/json",
    }
    base = DOKPLOY_URL.rstrip("/")
    url = f"{base}/api/project.all"

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        raw_projects = response.json()
        if not isinstance(raw_projects, list):
            raw_projects = []
    except requests.RequestException as exc:
        print(f"  ERROR: Failed to call /api/project.all: {exc}")
        raw_projects = []

    # --- Extract data from the nested structure ---
    projects = []
    applications = []
    databases = []

    for project in raw_projects:
        projects.append(project.get("name", "Unknown"))

        # Each project has an 'environments' list
        for env in project.get("environments", []):

            # Applications
            for app in env.get("applications", []):
                applications.append({
                    "name": app.get("name", "Unknown"),
                    "status": app.get("applicationStatus", "unknown"),
                })

            # Compose services (treated as applications)
            for svc in env.get("compose", []):
                applications.append({
                    "name": svc.get("name", "Unknown") + " (compose)",
                    "status": svc.get("composeStatus", svc.get("applicationStatus", "unknown")),
                })

            # Databases — Postgres, MySQL, Mongo, MariaDB, Redis
            db_types = {
                "postgres": env.get("postgres", []),
                "mysql": env.get("mysql", []),
                "mongo": env.get("mongo", []),
                "mariadb": env.get("mariadb", []),
                "redis": env.get("redis", []),
            }
            for db_type, db_list in db_types.items():
                for db in db_list:
                    databases.append({
                        "name": db.get("name", "Unknown"),
                        "type": db_type,
                        "status": db.get("applicationStatus", "unknown"),
                    })

    metrics = {
        "projects": projects,
        "applications": applications,
        "databases": databases,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    print(
        f"  Found {len(projects)} project(s), "
        f"{len(applications)} application(s), "
        f"{len(databases)} database(s)."
    )
    return metrics


# ---------------------------------------------------------------------------
# Function 2: Build the analysis prompt for Gemini
# ---------------------------------------------------------------------------
def build_prompt(metrics: dict) -> str:
    """
    Formats the collected metrics dictionary into a structured natural-language
    prompt that instructs Gemini to act as a DevOps assistant and produce a
    concise daily health report suitable for Telegram.

    Args:
        metrics: Dictionary returned by collect_metrics().

    Returns:
        A formatted prompt string ready to be sent to the Gemini API.
    """
    print("Building prompt for Gemini...")

    projects_text = (
        "\n".join(f"  - {name}" for name in metrics["projects"])
        if metrics["projects"] else "  (none found)"
    )

    apps_text = (
        "\n".join(
            f"  - {app['name']}: {app['status']}" for app in metrics["applications"]
        )
        if metrics["applications"] else "  (none found)"
    )

    db_text = (
        "\n".join(
            f"  - [{db['type'].upper()}] {db['name']}: {db['status']}"
            for db in metrics["databases"]
        )
        if metrics["databases"] else "  (none found)"
    )

    prompt = f"""You are a DevOps assistant. Analyze the following server metrics collected from a Dokploy deployment platform and produce a daily health report.

=== SERVER METRICS (collected at {metrics['timestamp']}) ===

PROJECTS:
{projects_text}

APPLICATIONS (name: status):
{apps_text}

DATABASES (type + name: status):
{db_text}

=== INSTRUCTIONS ===
Write a clear, concise daily server health report that includes:
1. One-sentence overall health summary.
2. Status of each application — label it as ✅ Healthy or ❌ Has Issues.
3. Status of each database — label it as ✅ Healthy or ❌ Has Issues.
4. Any anomalies or services with "error" or "idle" or unexpected status.
5. A short recommendation if anything looks wrong, or confirmation that everything is fine.

Style rules:
- Write for a Telegram message (plain text, no markdown headers like # or **).
- Use emojis for readability (✅, ❌, ⚠️, 🗄️, 🚀, etc.).
- Be concise — no filler sentences.
- Do NOT include a title line; the caller will prepend one.
"""
    return prompt


# ---------------------------------------------------------------------------
# Function 3: Send the prompt to Gemini and get the analysis
# ---------------------------------------------------------------------------
def analyze_with_gemini(prompt: str) -> str:
    """
    Sends the formatted prompt to the Google Gemini API using the google-genai
    SDK and returns the generated text response.

    Uses gemini-2.0-flash model. Retries once after 25 seconds if the API
    returns a 429 rate-limit error (free tier allows retrying after ~20s).

    Args:
        prompt: The prompt string built by build_prompt().

    Returns:
        The AI-generated report as a plain string, or an error message string
        if all attempts fail.
    """
    print("Calling Gemini for analysis...")

    client = genai.Client(api_key=GEMINI_API_KEY)
    model = "gemini-2.0-flash"

    for attempt in range(1, 3):  # Try up to 2 times
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
            )
            print("  Gemini analysis received successfully.")
            return response.text

        except Exception as exc:
            error_str = str(exc)
            # If rate-limited, wait and retry once
            if "429" in error_str and attempt == 1:
                print(f"  WARNING: Gemini rate limit hit. Waiting 25s before retry...")
                time.sleep(25)
                continue
            # Any other error or retry also failed — return error message
            error_msg = f"⚠️ Gemini analysis failed: {exc}"
            print(f"  ERROR: {error_msg}")
            return error_msg


# ---------------------------------------------------------------------------
# Function 4: Send the final report to Telegram
# ---------------------------------------------------------------------------
def send_to_telegram(report: str) -> None:
    """
    Sends the AI-generated report to the configured Telegram chat via the
    Telegram Bot API. Prepends a header line with the current date.

    Common reasons for failure:
      - 'chat not found': The TELEGRAM_CHAT_ID is wrong. For a private chat,
        use your numeric user ID (get it from t.me/userinfobot). For a group/
        channel, use the numeric ID like -1001234567890.

    Args:
        report: The text report to send (generated by analyze_with_gemini()).
    """
    print("Sending report to Telegram...")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    header = f"🖥️ Daily Server Report — {today}\n\n"
    full_message = header + report

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": full_message,
    }

    try:
        response = requests.post(url, json=payload, timeout=15)
        if not response.ok:
            print(
                f"  ERROR: Telegram responded with {response.status_code}: {response.text}\n"
                f"  HINT: 'chat not found' usually means TELEGRAM_CHAT_ID is wrong.\n"
                f"  HINT: For a private chat, get your numeric ID from @userinfobot on Telegram.\n"
                f"  HINT: For a group/channel, the ID looks like -1001234567890."
            )
        response.raise_for_status()
        print("  Report sent to Telegram successfully.")
    except requests.RequestException as exc:
        print(f"  ERROR: Failed to send Telegram message: {exc}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=== AI Server Health Agent Starting ===")

    metrics = collect_metrics()
    prompt = build_prompt(metrics)
    report = analyze_with_gemini(prompt)
    send_to_telegram(report)

    print("=== Agent run complete ===")
