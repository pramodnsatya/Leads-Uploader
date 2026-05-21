"""
Lead Uploader — internal Founderled tool.

The CSV columns are the source of truth. Maps directly to the Supabase `leads`
schema, dedupes via a three-tier identity key (email > LinkedIn > name+company),
and upserts in batches.
"""

import os
import re

import pandas as pd
import streamlit as st
from supabase import create_client


# ============================================================
# CONFIG
# ============================================================
st.set_page_config(
    page_title="Lead Uploader",
    page_icon="📥",
    layout="centered",
)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# Columns in the canonical CSV format, in order. Each maps directly to a
# column in the Supabase `leads` table.
LEAD_COLUMNS = [
    {"name": "client_name",            "label": "Client name",            "required": True},
    {"name": "sheet_name",             "label": "Sheet name",             "required": False},
    {"name": "persona_name",           "label": "Persona name",           "required": False},
    {"name": "normalized_first_name",  "label": "Normalized first name",  "required": False},
    {"name": "last_name",              "label": "Last name",              "required": False},
    {"name": "full_name",              "label": "Full name",              "required": False},
    {"name": "title",                  "label": "Title",                  "required": False},
    {"name": "email",                  "label": "Email",                  "required": False},
    {"name": "valid_emails",           "label": "Valid emails",           "required": False},
    {"name": "catch_all_valid",        "label": "Catch all valid",        "required": False},
    {"name": "linkedin",               "label": "LinkedIn",               "required": False},
    {"name": "company_website",        "label": "Company website",        "required": False},
    {"name": "cleaned_company_name",   "label": "Cleaned company name",   "required": False},
    {"name": "location",               "label": "Location",               "required": False},
    {"name": "company_linkedin",       "label": "Company LinkedIn",       "required": False},
]

COLUMN_ALIASES = {
    "client_name":           ["clientname", "client"],
    "sheet_name":            ["sheetname", "sourcesheet", "tab", "tabname"],
    "persona_name":          ["personaname", "persona"],
    "normalized_first_name": ["normalizedfirstname", "normalisedfirstname", "cleanfirstname", "cleanfname"],
    "last_name":             ["lastname", "lname", "surname"],
    "full_name":             ["fullname", "name"],
    "title":                 ["title", "jobtitle", "position"],
    "email":                 ["email", "emailaddress", "workemail"],
    "valid_emails":          ["validemails", "validemail"],
    "catch_all_valid":       ["catchallvalid", "catchall", "catchallemail"],
    "linkedin":              ["linkedin", "personlinkedin", "personlinkedinurl", "linkedinurl"],
    "company_website":       ["companywebsite", "website", "companydomain", "domain"],
    "cleaned_company_name":  ["cleanedcompanyname", "cleancompanyname", "normalizedcompanyname"],
    "location":              ["location"],
    "company_linkedin":      ["companylinkedin", "companylinkedinurl"],
}


# ============================================================
# STYLES
# ============================================================
BASE_CSS = """
<style>
[data-testid="stMainMenu"], [data-testid="stToolbar"],
[data-testid="stDeployButton"], [data-testid="stStatusWidget"],
button[kind="header"], footer, #MainMenu { display: none !important; }
header[data-testid="stHeader"] { background: transparent; height: 0; }

.block-container { padding-top: 1rem; padding-bottom: 3rem; max-width: 900px; }
h2, h3 { margin-top: 1.5rem !important; margin-bottom: 0.5rem !important; }
h2 { font-size: 1.35rem !important; }
h3 { font-size: 1.1rem !important; }

div[data-baseweb="select"] > div { min-height: 36px !important; }
.stTextInput input { padding: 0.4rem 0.75rem !important; }
.stTextArea textarea { padding: 0.5rem 0.75rem !important; }
</style>
"""

DARK_CSS = """
<style>
.stApp { background-color: #0e1117 !important; }
.stApp, .stApp p, .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6,
.stApp label, .stApp span, .stApp li, .stApp strong { color: #fafafa !important; }
[data-testid="stCaptionContainer"], small { color: #9aa0a6 !important; }
.stTextInput input, .stTextArea textarea { background-color: #262730 !important; color: #fafafa !important; border-color: #3d4049 !important; }
div[data-baseweb="select"] > div { background-color: #262730 !important; border-color: #3d4049 !important; }
div[data-baseweb="select"] * { color: #fafafa !important; }
ul[role="listbox"] { background-color: #262730 !important; border-color: #3d4049 !important; }
ul[role="listbox"] li { color: #fafafa !important; }
ul[role="listbox"] li[aria-selected="true"], ul[role="listbox"] li:hover { background-color: #3d4049 !important; }
[data-testid="stFileUploader"] section { background-color: #1c1f26 !important; border-color: #3d4049 !important; }
[data-testid="stFileUploader"] section * { color: #fafafa !important; }
code, [data-testid="stCodeBlock"], [data-testid="stJson"] { background-color: #262730 !important; color: #fafafa !important; }
[data-testid="stExpander"] { background-color: #1c1f26 !important; border-color: #3d4049 !important; }
[data-testid="stExpander"] * { color: #fafafa !important; }
[data-testid="stMetric"] { background-color: #1c1f26 !important; padding: 0.5rem; border-radius: 6px; }
[data-testid="stMetricValue"] { color: #fafafa !important; }
[data-testid="stMetricLabel"] { color: #9aa0a6 !important; }
.stButton > button { background-color: #262730 !important; color: #fafafa !important; border-color: #3d4049 !important; }
[data-testid="stDataFrame"] { background-color: #1c1f26 !important; }
[data-testid="stProgress"] > div { background-color: #262730 !important; }
</style>
"""


# ============================================================
# HELPERS
# ============================================================
def slugify(text: str) -> str:
    text = (text or "").lower().strip()
    text = re.sub(r"[^a-z0-9\s\-]", "", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def normalize_header(s: str) -> str:
    return re.sub(r"[\s_\-/]", "", (s or "").lower())


def auto_detect_match(col_name: str, csv_columns: list):
    aliases = set(COLUMN_ALIASES.get(col_name, []))
    aliases.add(normalize_header(col_name))
    for csv_col in csv_columns:
        if normalize_header(csv_col) in aliases:
            return csv_col
    return None


def normalize_linkedin(url: str) -> str:
    url = (url or "").strip().lower()
    url = re.sub(r"^https?://", "", url)
    url = re.sub(r"^www\.", "", url)
    url = url.split("?")[0].rstrip("/")
    return url


def safe_value(val):
    """Coerce a CSV value to a clean string. None for empty/NaN."""
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    s = str(val).strip()
    if s == "" or s.lower() == "nan":
        return None
    return s


def first_present(row: dict, *fields):
    """Return the first non-empty value among the given fields."""
    for f in fields:
        v = row.get(f)
        if v:
            return v
    return None


def make_dedupe_key(row: dict):
    """
    Three-tier identity key.

    Tier 1 — email: any of Email, Valid Emails, Catch All Valid (in that order)
    Tier 2 — LinkedIn URL (normalized)
    Tier 3 — full_name + company (website preferred, falling back to cleaned name)

    Returns None if the row has no usable identity at all.
    """
    # Tier 1 — email
    email = first_present(row, "email", "valid_emails", "catch_all_valid")
    if email:
        return f"email:{email.strip().lower()}"

    # Tier 2 — LinkedIn URL
    linkedin = row.get("linkedin")
    if linkedin:
        return f"li:{normalize_linkedin(linkedin)}"

    # Tier 3 — name + company
    name = row.get("full_name")
    if not name:
        first = row.get("normalized_first_name") or ""
        last = row.get("last_name") or ""
        name = f"{first} {last}".strip()

    company = first_present(row, "company_website", "cleaned_company_name")

    name_slug = slugify(name)
    company_slug = slugify(company)

    if name_slug and company_slug:
        return f"nc:{name_slug}|{company_slug}"

    return None


# ============================================================
# MAIN
# ============================================================
def main():
    # Theme state
    if "theme" not in st.session_state:
        st.session_state.theme = "light"
    is_dark = st.session_state.theme == "dark"

    st.markdown(BASE_CSS, unsafe_allow_html=True)
    if is_dark:
        st.markdown(DARK_CSS, unsafe_allow_html=True)

    # Top-right theme toggle
    spacer, toggle = st.columns([20, 1])
    with toggle:
        icon = "☀️" if is_dark else "🌙"
        next_mode = "light" if is_dark else "dark"
        if st.button(icon, key="theme_toggle", help=f"Switch to {next_mode} mode"):
            st.session_state.theme = next_mode
            st.rerun()

    st.title("📥 Lead Uploader")
    st.caption("Upload a CSV → confirm column mappings → push to Supabase.")

    # ---- 1. Upload ----
    st.subheader("1. Upload CSV")
    uploaded = st.file_uploader("Choose a CSV file", type=["csv"], label_visibility="collapsed")
    if uploaded is None:
        return

    try:
        df = pd.read_csv(uploaded)
    except Exception as e:
        st.error(f"Could not read CSV: {e}")
        return

    if len(df) == 0:
        st.error("The CSV has no rows.")
        return

    st.success(f"Loaded **{len(df):,}** rows from `{uploaded.name}`.")
    with st.expander("Preview source data"):
        st.dataframe(df.head(10), use_container_width=True)

    csv_columns = list(df.columns)

    # ---- 2. Column mapping ----
    st.subheader("2. Column mapping")
    st.caption("Auto-detected from CSV headers. Override anything that looks wrong.")

    mapping = {}
    for col in LEAD_COLUMNS:
        c1, c2 = st.columns([1, 2], vertical_alignment="center")
        with c1:
            marker = " <span style='color:#ef4444;'>＊</span>" if col["required"] else ""
            st.markdown(f"**{col['label']}**{marker}", unsafe_allow_html=True)
        with c2:
            options = ["— skip —"] + csv_columns
            detected = auto_detect_match(col["name"], csv_columns)
            default_idx = options.index(detected) if detected in options else 0
            mapping[col["name"]] = st.selectbox(
                label=col["label"],
                options=options,
                index=default_idx,
                key=f"map_{col['name']}",
                label_visibility="collapsed",
            )

    # ---- Validate mapping ----
    errors = []
    if mapping.get("client_name") == "— skip —":
        errors.append("Client name column must be mapped.")

    has_email_col = any(
        mapping.get(f) != "— skip —" for f in ("email", "valid_emails", "catch_all_valid")
    )
    has_linkedin = mapping.get("linkedin") != "— skip —"
    has_name = mapping.get("full_name") != "— skip —" or (
        mapping.get("normalized_first_name") != "— skip —"
        and mapping.get("last_name") != "— skip —"
    )
    has_company = (
        mapping.get("company_website") != "— skip —"
        or mapping.get("cleaned_company_name") != "— skip —"
    )
    if not (has_email_col or has_linkedin or (has_name and has_company)):
        errors.append(
            "At least one identity source needed: an email column, LinkedIn, "
            "or both (name + company)."
        )

    if errors:
        st.subheader("3. Issues to fix")
        for e in errors:
            st.error(e)
        return

    # ---- Build rows ----
    def build_row(csv_row) -> dict:
        row = {}
        for col in LEAD_COLUMNS:
            csv_col = mapping[col["name"]]
            if csv_col == "— skip —":
                row[col["name"]] = None
            else:
                row[col["name"]] = safe_value(csv_row[csv_col])

        # Normalize client_name to a slug (e.g., "Soona" -> "soona")
        if row.get("client_name"):
            row["client_name"] = slugify(row["client_name"])

        # Lowercase emails in place
        for f in ("email", "valid_emails", "catch_all_valid"):
            if row.get(f):
                row[f] = row[f].strip().lower()

        row["dedupe_key"] = make_dedupe_key(row)
        return row

    all_rows = [build_row(df.iloc[i]) for i in range(len(df))]

    # Drop rows that lack both client_name and a dedupe key
    rows_with_identity = [r for r in all_rows if r.get("client_name") and r["dedupe_key"]]
    missing_client = sum(1 for r in all_rows if not r.get("client_name"))
    missing_identity = sum(
        1 for r in all_rows if r.get("client_name") and not r["dedupe_key"]
    )

    # In-batch dedupe by (client_name, dedupe_key)
    seen = set()
    deduped_rows = []
    for r in rows_with_identity:
        k = (r["client_name"], r["dedupe_key"])
        if k not in seen:
            seen.add(k)
            deduped_rows.append(r)
    in_batch_dupes = len(rows_with_identity) - len(deduped_rows)

    # Categorize kept rows by tier
    tier_counts = {"email": 0, "linkedin": 0, "name+company": 0}
    for r in deduped_rows:
        k = r["dedupe_key"]
        if k.startswith("email:"):
            tier_counts["email"] += 1
        elif k.startswith("li:"):
            tier_counts["linkedin"] += 1
        else:
            tier_counts["name+company"] += 1

    st.subheader("3. Review")
    m1, m2, m3 = st.columns(3)
    m1.metric("Ready to upload", f"{len(deduped_rows):,}")
    m2.metric("Skipped (no identity)", f"{missing_identity + missing_client:,}")
    m3.metric("In-batch duplicates", f"{in_batch_dupes:,}")

    st.caption(
        f"**Dedupe tiers:** "
        f"{tier_counts['email']:,} by email · "
        f"{tier_counts['linkedin']:,} by LinkedIn · "
        f"{tier_counts['name+company']:,} by name+company"
    )

    if missing_client > 0:
        st.warning(f"{missing_client:,} row(s) skipped: no client_name value.")
    if missing_identity > 0:
        st.info(
            f"{missing_identity:,} row(s) skipped: had a client but no email, "
            f"LinkedIn, or name+company. Nothing identifies them as a person."
        )

    with st.expander("Preview first 3 rows as they will land in Supabase"):
        st.json(deduped_rows[:3])

    # ---- 4. Upload ----
    st.subheader("4. Upload")
    if not SUPABASE_URL or not SUPABASE_KEY:
        st.error(
            "Supabase credentials not configured. "
            "Set `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` env vars on Railway."
        )
        return

    if st.button("🚀 Push to Supabase", type="primary", use_container_width=True):
        try:
            client_sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        except Exception as e:
            st.error(f"Failed to connect to Supabase: {e}")
            return

        batch_size = 500
        success_count = 0
        error_count = 0
        progress = st.progress(0.0)
        status = st.empty()

        for i in range(0, len(deduped_rows), batch_size):
            batch = deduped_rows[i : i + batch_size]
            try:
                client_sb.table("leads").upsert(
                    batch, on_conflict="client_name,dedupe_key"
                ).execute()
                success_count += len(batch)
            except Exception as e:
                error_count += len(batch)
                st.warning(f"Batch starting at row {i + 1} failed: {e}")

            done = min(i + batch_size, len(deduped_rows))
            progress.progress(done / len(deduped_rows))
            status.text(f"Processed {done:,}/{len(deduped_rows):,}")

        progress.progress(1.0)
        if error_count == 0:
            st.success(f"✅ Upserted **{success_count:,}** rows successfully.")
            st.balloons()
        else:
            st.warning(
                f"⚠️ Upserted {success_count:,} rows. {error_count:,} rows failed — see warnings above."
            )


main()
