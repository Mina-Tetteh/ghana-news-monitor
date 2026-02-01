"""
Ghana Cash Crop News Monitor
Searches for news, filters with Claude AI, stores in Google Sheets.
Deploy on Railway for one-time historical backfill.
"""

import os
import sys
import json
import time
import requests
from datetime import datetime, timedelta
from anthropic import Anthropic
import gspread
from google.oauth2.service_account import Credentials

# =============================================================================
# CONFIGURATION
# =============================================================================

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

SEARCH_QUERIES = [
    "Ghana cocoa news",
    "COCOBOD announcement",
    "Ghana shea butter industry",
    "Ghana cashew export",
    "Ghana coffee farming",
    "Ghana cocoa investment funding",
    "Ghana agriculture startup funding",
    "cocoa farmer financing Ghana",
    "shea butter investment Africa",
    "Hershey cocoa Ghana",
    "Tony's Chocolonely Ghana",
    "ECOM cocoa Ghana",
    "World Cocoa Foundation Ghana",
    "Ghana Cocoa Board",
    "cocoa price Ghana",
    "sustainable cocoa Ghana",
]

# =============================================================================
# ENVIRONMENT CHECK
# =============================================================================

def check_environment():
    """Validate all required environment variables exist."""
    required = {
        "SERPER_API_KEY": SERPER_API_KEY,
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "GOOGLE_SHEETS_ID": GOOGLE_SHEETS_ID,
        "GOOGLE_CREDENTIALS_JSON": GOOGLE_CREDENTIALS_JSON,
    }

    missing = [name for name, value in required.items() if not value]

    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}")
        print("\nSet these in Railway Dashboard → Variables")
        sys.exit(1)

    print("✅ All environment variables found")

# =============================================================================
# SERPER API - NEWS SEARCH
# =============================================================================

def search_news(query: str, date_from: str, date_to: str, num_results: int = 20) -> list:
    """Search for news using Serper.dev API."""
    url = "https://google.serper.dev/news"

    search_query = f"{query} after:{date_from} before:{date_to}"

    headers = {
        "X-API-KEY": SERPER_API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "q": search_query,
        "gl": "gh",
        "num": num_results
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        return response.json().get("news", [])
    except Exception as e:
        print(f"  ⚠️ Search error: {e}")
        return []

# =============================================================================
# CLAUDE AI - ARTICLE ANALYSIS
# =============================================================================

def analyze_articles_with_claude(articles: list) -> list:
    """Use Claude to filter and categorize articles."""
    if not articles:
        return []

    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""Analyze these news articles about Ghana's agricultural industry.

For each RELEVANT article (about cocoa, shea, cashew, coffee, or agricultural funding/investment in Ghana/West Africa), extract:

1. relevance: true/false - Is this about Ghana cash crops or African agricultural investment?
2. category: "cocoa" | "shea" | "cashew" | "coffee" | "general_agriculture" | "funding_investment"
3. companies_mentioned: List of company/organization names
4. funding_amount: If mentioned (e.g., "$5M", "GHS 2 million"), else null
5. key_entities: Important people, government bodies, NGOs
6. summary: 1-2 sentence summary

Return ONLY a JSON array:
[
  {{
    "original_title": "...",
    "original_link": "...",
    "original_date": "...",
    "original_source": "...",
    "relevance": true,
    "category": "cocoa",
    "companies_mentioned": ["COCOBOD"],
    "funding_amount": null,
    "key_entities": ["Dr. Joseph Aidoo"],
    "summary": "..."
  }}
]

Articles:
{json.dumps(articles, indent=2)}

Return ONLY the JSON array, no other text."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}]
        )

        text = response.content[0].text

        # Clean response
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        return json.loads(text.strip())
    except Exception as e:
        print(f"  ⚠️ Claude API error: {e}")
        return []

# =============================================================================
# GOOGLE SHEETS
# =============================================================================

def get_sheets_client():
    """Create authorized Google Sheets client."""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)

    return gspread.authorize(creds)


def get_existing_urls(sheet) -> set:
    """Get URLs already in sheet to avoid duplicates."""
    try:
        return set(sheet.col_values(8)[1:])  # Column H, skip header
    except:
        return set()


def append_to_sheet(articles: list) -> int:
    """Add articles to Google Sheet. Returns count added."""
    if not articles:
        return 0

    try:
        client = get_sheets_client()
        sheet = client.open_by_key(GOOGLE_SHEETS_ID).worksheet("News Data")

        existing_urls = get_existing_urls(sheet)

        rows = []
        for article in articles:
            if not article.get("relevance"):
                continue

            url = article.get("original_link", "")
            if url in existing_urls:
                continue

            rows.append([
                article.get("original_date", ""),
                article.get("original_title", ""),
                article.get("original_source", ""),
                article.get("category", ""),
                ", ".join(article.get("companies_mentioned", [])),
                article.get("funding_amount") or "",
                article.get("summary", ""),
                url,
                ", ".join(article.get("key_entities", [])),
                datetime.now().strftime("%Y-%m-%d %H:%M")
            ])

        if rows:
            sheet.append_rows(rows)

        return len(rows)
    except Exception as e:
        print(f"  ⚠️ Sheets error: {e}")
        return 0

# =============================================================================
# BACKFILL
# =============================================================================

def run_backfill(start_date: str = "2025-11-01"):
    """Run historical news backfill."""
    end_date = datetime.now().strftime("%Y-%m-%d")

    print("=" * 60)
    print("📚 HISTORICAL BACKFILL")
    print(f"   Date range: {start_date} to {end_date}")
    print("=" * 60)

    all_articles = []

    for i, query in enumerate(SEARCH_QUERIES):
        print(f"\n[{i+1}/{len(SEARCH_QUERIES)}] Searching: {query}")
        articles = search_news(query, start_date, end_date)
        print(f"  → Found {len(articles)} articles")
        all_articles.extend(articles)
        time.sleep(1)

    # Deduplicate by URL
    seen = set()
    unique = []
    for article in all_articles:
        url = article.get("link", "")
        if url not in seen:
            seen.add(url)
            unique.append(article)

    print(f"\n📊 Total unique articles: {len(unique)}")
    print("🤖 Analyzing with Claude AI...")

    # Process in batches
    analyzed = []
    batch_size = 15

    for i in range(0, len(unique), batch_size):
        batch = unique[i:i + batch_size]
        print(f"  Processing batch {i//batch_size + 1}...")
        results = analyze_articles_with_claude(batch)
        analyzed.extend(results)
        time.sleep(1)

    relevant = sum(1 for a in analyzed if a.get("relevance"))
    print(f"\n✅ Relevant articles: {relevant}")

    print("📤 Uploading to Google Sheets...")
    added = append_to_sheet(analyzed)
    print(f"✅ Added {added} new articles")

    print("\n" + "=" * 60)
    print("✅ BACKFILL COMPLETE!")
    print("=" * 60)

# =============================================================================
# MAIN
# =============================================================================

def main():
    print("\n" + "🌿" * 30)
    print("  GHANA CASH CROP NEWS MONITOR")
    print("🌿" * 30 + "\n")

    check_environment()

    mode = sys.argv[1] if len(sys.argv) > 1 else os.getenv("RUN_MODE", "backfill")
    start_date = os.getenv("BACKFILL_START_DATE", "2025-11-01")

    if mode == "backfill":
        run_backfill(start_date)
        print("\n🏁 Done! You can delete this Railway service now.")
        print("   Your n8n workflow will handle ongoing monitoring.")
    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python main.py backfill")


if __name__ == "__main__":
    main()
