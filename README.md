# Lead Uploader

Internal Founderled tool for pushing CSV lead lists into the Supabase `leads` table.

## What it does

1. Upload a CSV
2. Pick the client and (optionally) the source sheet/tab name
3. Set tags (list, persona, themes, vertical, segment) — applied to every row
4. Map each Supabase column to a CSV column (auto-detected, override as needed)
5. Preview, validate, and push to Supabase via batched upsert

The unique constraint on `(client, email)` means re-running the same file is idempotent — existing rows update, new rows insert, nothing duplicates.

## Local development

```bash
cp .env.example .env
# fill in SUPABASE_URL, SUPABASE_SERVICE_KEY, APP_PASSWORD

pip install -r requirements.txt
streamlit run app.py
```

Open http://localhost:8501.

## Deploying to Railway

1. Push this folder to a private GitHub repo.
2. Railway → New Project → Deploy from GitHub repo → pick the repo.
3. In **Variables**, add:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`
   - `APP_PASSWORD`
4. Railway auto-detects the `Dockerfile` and builds.
5. Under **Settings → Networking → Generate Domain** to get a shareable URL.

## Maintenance

- The list of clients in the dropdown lives in `KNOWN_CLIENTS` at the top of `app.py`. Add new clients there.
- The list of mappable columns lives in `LEAD_COLUMNS`. If you add a column to the Supabase `leads` table, add it here too.
- Auto-detect aliases live in `COLUMN_ALIASES`. Add new aliases when CSVs use unusual headers.
