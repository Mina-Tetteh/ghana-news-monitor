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

    # Build article list with full field names
    article_list = []
    for a in articles:
        article_list.append({
            "title": a.get("title", "")[:100],
            "link": a.get("link", ""),
            "date": a.get("date", ""),
            "source": a.get("source", "")
        })

    prompt = f"""Analyze these articles about Ghana agriculture. Return ONLY a JSON array.

For each article, output this exact JSON structure:
{{"original_title":"<title>","original_link":"<link>","original_date":"<date>","original_source":"<source>","relevance":true,"category":"cocoa","companies_mentioned":[],"funding_amount":null,"key_entities":[],"summary":"<brief>"}}

Categories: cocoa, shea, cashew, coffee, general_agriculture, funding_investment
Set relevance=true only if about Ghana/Africa cash crops or agricultural investment.

Articles:
{json.dumps(article_list)}

JSON array:"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )

        text = response.content[0].text
        print(f"    Raw response length: {len(text)} chars")

        cleaned = clean_json_response(text)
        results = parse_json_safely(cleaned)

        if results:
            print(f"    Parsed {len(results)} items")
            return results

        # Debug: show first 200 chars of response
        print(f"    Parse failed. Response preview: {text[:200]}...")

        if text and retry_count < 1:
            print(f"    Retrying...")
            time.sleep(10)
            return analyze_articles_with_claude(articles, retry_count + 1)

        return []

    except Exception as e:
        error_msg = str(e)
        if "rate_limit" in error_msg.lower() and retry_count < 3:
            wait = 180 * (retry_count + 1)
            print(f"    Rate limited, waiting {wait//60} min...")
            time.sleep(wait)
            return analyze_articles_with_claude(articles, retry_count + 1)
        print(f"  ⚠️ Claude error: {e}")
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
        print("  No articles to add")
        return 0

    print(f"  Processing {len(articles)} articles for sheet...")

    try:
        client = get_sheets_client()
        sheet = client.open_by_key(GOOGLE_SHEETS_ID).worksheet("News Data")

        existing_urls = get_existing_urls(sheet)
        print(f"  Found {len(existing_urls)} existing URLs")

        rows = []
        skipped_relevance = 0
        skipped_duplicate = 0

        for article in articles:
            if not article.get("relevance"):
                skipped_relevance += 1
                continue

            url = article.get("original_link", "")
            if url in existing_urls:
                skipped_duplicate += 1
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

        print(f"  Skipped: {skipped_relevance} not relevant, {skipped_duplicate} duplicates")
        print(f"  Adding {len(rows)} new rows to sheet...")

        if rows:
            sheet.append_rows(rows)
            print(f"  ✓ Successfully added {len(rows)} rows")

        return len(rows)
    except Exception as e:
        print(f"  ⚠️ Sheets error: {e}")
        import traceback
        traceback.print_exc()
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

    # Process in tiny batches (3 articles) with 60s delays
    analyzed = []
    batch_size = 3  # Tiny batches = fewer tokens per request
    total_batches = (len(unique) + batch_size - 1) // batch_size

    print(f"  Processing {len(unique)} articles in {total_batches} batches of {batch_size}")
    print(f"  Estimated time: ~{total_batches} minutes\n")

    for i in range(0, len(unique), batch_size):
        batch = unique[i:i + batch_size]
        batch_num = i // batch_size + 1
        print(f"  [{batch_num}/{total_batches}] Analyzing {len(batch)} articles...")
        results = analyze_articles_with_claude(batch)
        analyzed.extend(results)
        print(f"    ✓ {len(results)} results")

        # Wait 60 seconds between batches
        if i + batch_size < len(unique):
            print(f"    Waiting 60s...")
            time.sleep(60)

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
