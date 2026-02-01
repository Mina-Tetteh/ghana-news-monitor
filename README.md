# Ghana Cash Crop News Monitor

AI-powered news tracker for Ghana's cocoa, shea, cashew, and coffee industries.

## What It Does

- Searches 16 keywords across Google News (via Serper.dev)
- Uses Claude AI to filter and categorize relevant articles
- Stores results in Google Sheets
- Tracks funding announcements and investments

## Environment Variables

Set these in Railway (or .env file for local testing):

| Variable | Description |
|----------|-------------|
| `SERPER_API_KEY` | From serper.dev (free 2,500 credits) |
| `ANTHROPIC_API_KEY` | From console.anthropic.com |
| `GOOGLE_SHEETS_ID` | The ID from your Google Sheet URL |
| `GOOGLE_CREDENTIALS_JSON` | Full JSON content of service account key |
| `BACKFILL_START_DATE` | Optional, defaults to 2025-11-01 |

## Google Sheet Setup

1. Create a new Google Sheet
2. Rename first tab to: `News Data`
3. Add headers in Row 1:
   - A: Date
   - B: Title
   - C: Source
   - D: Category
   - E: Companies Mentioned
   - F: Funding Amount
   - G: Summary
   - H: URL
   - I: Key Entities
   - J: Added At

4. Share sheet with your service account email (as Editor)

## Deploy to Railway

1. Push this code to GitHub
2. Create new Railway project from GitHub repo
3. Add all environment variables
4. Deploy - it runs once and exits

## Run Locally

```bash
cp .env.example .env
# Edit .env with your real keys
pip install -r requirements.txt
python main.py backfill
```

## Categories

- `cocoa` - Cocoa industry news
- `shea` - Shea butter industry
- `cashew` - Cashew industry
- `coffee` - Coffee industry
- `general_agriculture` - Cross-crop news
- `funding_investment` - Funding and investment news
