"""
AI Server Health Agent
======================
Collects metrics from a Dokploy instance, analyzes them with Google Gemini,
and sends a daily health report to a Telegram channel.

Environment variables are injected at runtime by Dokploy — no .env file is used.
"""

import os
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
    Calls multiple Dokploy REST API endpoints to gather a snapshot of the
    current state of all projects, applications, and databases.

    Dokploy exposes tRPC-style REST endpoints:
      - GET /api/project.all
      - GET /api/application.all
      - GET /api/postgres.all  /api/mysql.all  /api/mongo.all

    Returns a dictionary with:
        - projects:     list of project names
        - applications: list of dicts with 'name' and 'status'
        - databases:    list of dicts with 'name', 'type', and 'status'
        - timestamp:    ISO-8601 UTC timestamp of when the data was collected
    """
    print("Collecting metrics from Dokploy...")

    headers = {
        "x-api-key": DOKPLOY_API_KEY,
        "Content-Type": "application/json",
    }
    base = DOKPLOY_URL.rstrip("/")

    def _get(endpoint: str) -> list:
        """Make a GET request to a Dokploy endpoint and return a list of items."""
        url = f"{base}{endpoint}"
        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                return data
            # Handle wrapped responses
            for key in ("items", "data", "result"):
                if key in data and isinstance(data[key], list):
                    return data[key]
            return [data] if isinstance(data, dict) else []
        except requests.RequestException as exc:
            print(f"  WARNING: Failed to call {endpoint}: {exc}")
            return []

    # --- Projects ---
    raw_projects = _get("/api/project.all")
    projects = [p.get("name", "Unknown") for p in raw_projects]

    # --- Applications ---
    # Try /api/application.all first; if empty, extract services from each project
    applications = []
    raw_apps = _get("/api/application.all")
    if raw_apps:
        applications = [
            {
                "name": app.get("name", "Unknown"),
                "status": app.get("applicationStatus", app.get("status", "unknown")),
            }
            for app in raw_apps
        ]
    else:
        # Fallback: pull applications embedded in each project object
        print("  INFO: /api/application.all not available, extracting from project data...")
        for project in raw_projects:
            for app in project.get("applications", []):
                applications.append(
                    {
                        "name": app.get("name", "Unknown"),
                        "status": app.get("applicationStatus", app.get("status", "unknown")),
                    }
                )

    # --- Databases (Postgres, MySQL, Mongo) ---
    databases = []
    db_endpoints = {
        "postgres": "/api/postgres.all",
        "mysql": "/api/mysql.all",
        "mongo": "/api/mongo.all",
    }
    for db_type, endpoint in db_endpoints.items():
        for db in _get(endpoint):
            databases.append(
                {
                    "name": db.get("name", "Unknown"),
                    "type": db_type,
                    "status": db.get("databaseStatus", db.get("status", "unknown")),
                }
            )
        # Also try pulling databases embedded in project data
        if not databases:
            for project in raw_projects:
                for db in project.get(f"{db_type}s", []):
                    databases.append(
                        {
                            "name": db.get("name", "Unknown"),
                            "type": db_type,
                            "status": db.get("databaseStatus", db.get("status", "unknown")),
                        }
                    )

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
5. A short recommendation if anything looks wrong, or a confirmation that everything is fine.

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
    Sends the formatted prompt to the Google Gemini API using the new
    google-genai SDK and returns the generated text response.

    Uses the gemini-2.0-flash model (current stable free-tier model).

    Args:
        prompt: The prompt string built by build_prompt().

    Returns:
        The AI-generated report as a plain string, or an error message string
        if the API call fails.
    """
    print("Calling Gemini for analysis...")

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )
        report = response.text
        print("  Gemini analysis received successfully.")
        return report
    except Exception as exc:
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
        # Print full error body so it's visible in Dokploy logs
        if not response.ok:
            print(f"  ERROR: Telegram responded with {response.status_code}: {response.text}")
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
