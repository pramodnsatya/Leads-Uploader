-- ============================================================
-- Monthly audit queries
-- ============================================================
-- Run these in Supabase → SQL Editor periodically (monthly is a good cadence)
-- to catch potential dedupe collisions and data quality issues.


-- ---- 1. Same person possibly stored under different keys ----
-- Finds rows that share the same name+company within a client but have different
-- dedupe_keys. These are likely the same person captured under different identity tiers
-- (e.g., one row keyed by email, another keyed by linkedin or name+company+title).
-- Review and merge as appropriate.
SELECT
  client,
  LOWER(TRIM(full_name)) AS norm_name,
  LOWER(TRIM(company_name)) AS norm_company,
  COUNT(*) AS row_count,
  ARRAY_AGG(dedupe_key) AS dedupe_keys,
  ARRAY_AGG(email) AS emails,
  ARRAY_AGG(person_linkedin_url) AS linkedins,
  ARRAY_AGG(job_title) AS titles
FROM leads
WHERE full_name IS NOT NULL
  AND company_name IS NOT NULL
  AND TRIM(full_name) <> ''
  AND TRIM(company_name) <> ''
GROUP BY client, LOWER(TRIM(full_name)), LOWER(TRIM(company_name))
HAVING COUNT(*) > 1
ORDER BY row_count DESC, client, norm_company, norm_name;


-- ---- 2. Suspicious name+company+title rows ----
-- Rows that ended up using the tier-3 fallback even though they have a LinkedIn URL.
-- This shouldn't happen — if linkedin existed, the row should have been keyed by it.
-- Indicates either a bad ingest or a row that was tier-3 keyed and later had linkedin added.
SELECT
  client, dedupe_key, email, person_linkedin_url, full_name, company_name, job_title, imported_at
FROM leads
WHERE dedupe_key LIKE 'nct:%'
  AND person_linkedin_url IS NOT NULL
  AND TRIM(person_linkedin_url) <> ''
ORDER BY imported_at DESC;


-- ---- 3. Tier breakdown per client ----
-- Health check: how many leads are in each dedupe tier per client.
-- A client with 80%+ tier-3 (name+company+title) keys is fragile — those collide more easily.
-- Indicates lead-list quality is low (missing emails and LinkedIn URLs).
SELECT
  client,
  COUNT(*) FILTER (WHERE dedupe_key LIKE 'email:%') AS email_keyed,
  COUNT(*) FILTER (WHERE dedupe_key LIKE 'li:%') AS linkedin_keyed,
  COUNT(*) FILTER (WHERE dedupe_key LIKE 'nct:%') AS name_company_title_keyed,
  COUNT(*) AS total
FROM leads
GROUP BY client
ORDER BY total DESC;


-- ---- 4. Find a specific person across clients (for reuse) ----
-- Replace the email or domain to find someone you might want to pull into another client.
-- Example: find Sarah at Stripe across our entire portfolio
SELECT client, dedupe_key, full_name, job_title, email, tags, imported_at
FROM leads
WHERE LOWER(full_name) LIKE '%sarah chen%'
  AND LOWER(company_name) LIKE '%stripe%'
ORDER BY imported_at DESC;
