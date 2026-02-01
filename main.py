"""
Ghana Cash Crop News Monitor
Searches for news, filters with Claude AI, stores in Google Sheets.
Deploy on Railway for one-time historical backfill.
"""

import os
import sys
import json
import time
import re
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

def clean_json_response(text: str) -> str:
    """Clean and fix common JSON issues from Claude responses."""
    # Extract from code blocks if present
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]

    text = text.strip()

    # Remove trailing commas before ] or }
    text = re.sub(r',\s*([}\]])', r'\1', text)

    # Fix common issues with newlines in strings
    # Replace actual newlines within strings with \n escape
    text = re.sub(r'(?<=": ")(.*?)(?="[,}\]])', lambda m: m.group(1).replace('\n', '\\n'), text, flags=re.DOTALL)

    return text


def parse_json_safely(text: str) -> list:
    """Attempt to parse JSON with multiple fallback strategies."""
    # Strategy 1: Direct parse
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        return []
    except json.JSONDecodeError:
        pass

    # Strategy 2: Extract array portion and parse
    try:
        match = re.search(r'\[[\s\S]*\]', text)
        if match:
            cleaned = re.sub(r',\s*([}\]])', r'\1', match.group())
            result = json.loads(cleaned)
            if isinstance(result, list):
                return result
    except json.JSONDecodeError:
        pass

    # Strategy 3: Try to fix and parse individual objects
    try:
        # Find all JSON-like objects
        objects = re.findall(r'\{[^{}]*\}', text)
        results = []
        for obj in objects:
            try:
                cleaned = re.sub(r',\s*([}\]])', r'\1', obj)
                parsed = json.loads(cleaned)
                results.append(parsed)
            except json.JSONDecodeError:
                continue
        if results:
            return results
    except Exception:
        pass

    return []


def analyze_articles_with_claude(articles: list, retry_count: int = 0) -> list:
    """Use Claude to filter and categorize articles."""
    if not articles:
        return []

    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    # Simplify article data to reduce tokens
    simplified_articles = []
    for a in articles:
        simplified_articles.append({
            "title": a.get("title", ""),
            "link": a.get("link", ""),
            "date": a.get("date", ""),
            "source": a.get("source", ""),
            "snippet": a.get("snippet", "")[:200] if a.get("snippet") else ""
        })

    prompt = f"""Analyze these news articles about Ghana agriculture.

For each article, determine if it's relevant to Ghana cash crops (cocoa, shea, cashew, coffee) or agricultural funding.

Return a JSON array with this exact structure (no extra text):
[{{"original_title":"article title","original_link":"url","original_date":"date","original_source":"source","relevance":true,"category":"cocoa","companies_mentioned":["Company"],"funding_amount":null,"key_entities":["Person"],"summary":"Brief summary"}}]

Categories: cocoa, shea, cashew, coffee, general_agriculture, funding_investment

Articles:
{json.dumps(simplified_articles)}

Return ONLY valid JSON array:"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}]
        )

        text = response.content[0].text
        cleaned = clean_json_response(text)
        results = parse_json_safely(cleaned)

        if results:
            return results

        # If parsing failed but we have text, log it for debugging
        if text and retry_count < 1:
            print(f"    Retrying batch due to parse error...")
            time.sleep(5)
            return analyze_articles_with_claude(articles, retry_count + 1)

        return []

    except Exception as e:
        error_msg = str(e)
        if "rate_limit" in error_msg.lower() and retry_count < 2:
            print(f"    Rate limited, waiting 30s and retrying...")
            time.sleep(30)
            return analyze_articles_with_claude(articles, retry_count + 1)
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

            # Safely handle list fields that might be strings or None
            companies = article.get("companies_mentioned", [])
            if isinstance(companies, str):
                companies = [companies]
            elif not isinstance(companies, list):
                companies = []

            entities = article.get("key_entities", [])
            if isinstance(entities, str):
                entities = [entities]
            elif not isinstance(entities, list):
                entities = []

            rows.append([
                str(article.get("original_date", "")),
                str(article.get("original_title", "")),
                str(article.get("original_source", "")),
                str(article.get("category", "")),
                ", ".join(str(c) for c in companies),
                str(article.get("funding_amount") or ""),
                str(article.get("summary", "")),
                url,
                ", ".join(str(e) for e in entities),
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

    # Process in batches (smaller batches + longer delays to avoid rate limits)
    analyzed = []
    batch_size = 8  # Reduced from 15 to stay under token limits
    total_batches = (len(unique) + batch_size - 1) // batch_size

    for i in range(0, len(unique), batch_size):
        batch = unique[i:i + batch_size]
        batch_num = i // batch_size + 1
        print(f"  Processing batch {batch_num}/{total_batches}...")
        results = analyze_articles_with_claude(batch)
        analyzed.extend(results)
        # Wait 15 seconds between batches to respect rate limits (30k tokens/min)
        if i + batch_size < len(unique):
            print(f"    Waiting 15s to respect rate limits...")
            time.sleep(15)

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
