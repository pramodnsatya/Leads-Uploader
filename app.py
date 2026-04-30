"""
Lead Uploader — internal Founderled tool.

Reads a CSV, lets you map columns to the Supabase `leads` schema,
configure tags as key/value inputs, preview, and upsert in batches.
"""

import os
import re
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from supabase import create_client


# ============================================================
# CONFIG
# ============================================================
st.set_page_config(page_title="Lead Uploader", page_icon="📥", layout="wide")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

# Schema definition. These are the columns from the Supabase `leads` table
# that get populated from the CSV. Columns handled separately (id, client,
# tags, source_file, source_sheet, imported_at) are not in this list.
LEAD_COLUMNS = [
    {"name": "first_name", "label": "First name", "required": False},
    {"name": "normalized_first_name", "label": "Normalized first name", "required": False},
    {"name": "last_name", "label": "Last name", "required": False},
    {"name": "full_name", "label": "Full name", "required": False},
    {"name": "email", "label": "Email", "required": True},
    {"name": "person_linkedin_url", "label": "Person LinkedIn URL", "required": False},
    {"name": "company_name", "label": "Company name", "required": False},
    {"name": "normalized_company_name", "label": "Normalized company name", "required": False},
    {"name": "company_domain", "label": "Company domain / website", "required": False},
    {"name": "company_linkedin_url", "label": "Company LinkedIn URL", "required": False},
    {"name": "job_title", "label": "Job title", "required": False},
    {"name": "location_raw", "label": "Location (raw)", "required": False},
    {"name": "city", "label": "City", "required": False},
    {"name": "country", "label": "Country", "required": False},
    {"name": "employee_count", "label": "Employee count", "required": False, "type": "int"},
    {"name": "employee_range", "label": "Employee range", "required": False},
    {"name": "number_of_connections", "label": "Number of connections", "required": False, "type": "int"},
    {"name": "mx_records", "label": "MX records", "required": False, "type": "array"},
]

# Used as the dropdown for the client field. Keep these slugs in sync with
# how the rest of your stack refers to each client (lowercase, hyphenated).
KNOWN_CLIENTS = [
    "dagster",
    "wispr-flow",
    "soona",
    "kastle",
    "adaptational-ai",
    "sunset",
    "arist",
    "epsilon3",
]

# Fuzzy aliases for auto-detecting CSV → Supabase column mappings.
COLUMN_ALIASES = {
    "first_name": ["firstname", "fname"],
    "normalized_first_name": ["normalizedfirstname", "cleanfirstname", "cleanfname"],
    "last_name": ["lastname", "lname", "surname"],
    "full_name": ["fullname", "name"],
    "email": ["email", "emailaddress", "workemail", "emails"],
    "person_linkedin_url": ["personlinkedinurl", "personlinkedin", "linkedinurl", "linkedin"],
    "company_name": ["companyname", "company"],
    "normalized_company_name": ["normalizedcompanyname", "cleancompanyname"],
    "company_domain": ["companydomain", "companywebsite", "domain", "website", "companydomainwebsite"],
    "company_linkedin_url": ["companylinkedinurl", "companylinkedin"],
    "job_title": ["jobtitle", "title", "position"],
    "location_raw": ["location", "locationraw"],
    "city": ["city"],
    "country": ["country"],
    "employee_count": ["employeecount", "employees", "headcount"],
    "employee_range": ["employeerange", "companysize"],
    "number_of_connections": ["numberofconnections", "connections", "numconnections"],
    "mx_records": ["mxrecords", "mx", "mxdomain"],
}


# ============================================================
# HELPERS
# ============================================================
def slugify(text: str) -> str:
    """Lowercase, hyphenated, ASCII-only slug."""
    text = (text or "").lower().strip()
    text = re.sub(r"[^a-z0-9\s\-]", "", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def normalize_header(s: str) -> str:
    """Normalize a column header for matching: lowercase, no spaces/underscores/hyphens."""
    return re.sub(r"[\s_\-/]", "", (s or "").lower())


def auto_detect_match(supabase_col: str, csv_columns: list) -> str | None:
    """Find the best CSV column for a given Supabase column name."""
    aliases = set(COLUMN_ALIASES.get(supabase_col, []))
    aliases.add(normalize_header(supabase_col))

    for csv_col in csv_columns:
        if normalize_header(csv_col) in aliases:
            return csv_col
    return None


def build_tags(list_slug, persona, themes_str, vertical, segment, custom_tags):
    """Combine all tag inputs into the prefixed tag array."""
    tags = []
    if list_slug:
        tags.append(f"list:{slugify(list_slug)}")
    if persona:
        tags.append(f"persona:{slugify(persona)}")
    if themes_str:
        for theme in themes_str.split(","):
            theme = theme.strip()
            if theme:
                tags.append(f"theme:{slugify(theme)}")
    if vertical:
        tags.append(f"vertical:{slugify(vertical)}")
    if segment:
        tags.append(f"segment:{slugify(segment)}")
    if custom_tags:
        for line in custom_tags.splitlines():
            line = line.strip()
            if line and ":" in line:
                tags.append(line)  # pre-formatted, no slugify
    # de-dupe while preserving order
    seen = set()
    out = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def safe_value(val, col_type=None):
    """Coerce a CSV value to a clean DB value. Returns None for empty/NaN."""
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    s = str(val).strip()
    if s == "" or s.lower() == "nan":
        return None

    if col_type == "int":
        # strip commas, decimals
        try:
            return int(float(s.replace(",", "")))
        except (ValueError, TypeError):
            return None
    if col_type == "array":
        return [x.strip() for x in s.split(",") if x.strip()]
    return s


# ============================================================
# AUTH GATE
# ============================================================
def check_password() -> bool:
    if "auth_ok" not in st.session_state:
        st.session_state.auth_ok = False

    if st.session_state.auth_ok:
        return True

    if not APP_PASSWORD:
        st.error("APP_PASSWORD env var is not set. Refusing to start without auth.")
        return False

    st.title("📥 Lead Uploader")
    pw = st.text_input("Password", type="password")
    if st.button("Enter"):
        if pw == APP_PASSWORD:
            st.session_state.auth_ok = True
            st.rerun()
        else:
            st.error("Wrong password.")
    return False


# ============================================================
# MAIN
# ============================================================
def main():
    st.title("📥 Lead Uploader")
    st.caption("Upload a CSV → map columns → set tags → push to Supabase.")

    # ---- 1. Upload ----
    st.header("1. Upload CSV")
    uploaded = st.file_uploader("Choose a CSV file", type=["csv"])
    if uploaded is None:
        st.info("Upload a CSV to begin.")
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
    with st.expander("Preview source data", expanded=False):
        st.dataframe(df.head(10))

    csv_columns = list(df.columns)

    # ---- 2. Metadata ----
    st.header("2. Metadata")
    col1, col2 = st.columns(2)

    with col1:
        client_choice = st.selectbox(
            "Client *",
            options=KNOWN_CLIENTS + ["+ Add new client"],
            help="Which client this list is for. Stored in the `client` column.",
        )
        if client_choice == "+ Add new client":
            new_client = st.text_input("New client slug", placeholder="e.g. acme-corp")
            client = slugify(new_client) if new_client else ""
        else:
            client = client_choice

    with col2:
        source_sheet = st.text_input(
            "Source sheet/tab name",
            placeholder="e.g. Engineering, or leave blank for single-tab files",
            help="The original Google Sheet tab this CSV came from.",
        )

    # ---- 3. Tags ----
    st.header("3. Tags")
    st.caption(
        "These tags are applied to **every row** in this upload. "
        "Don't include the prefix (`list:`, `persona:`, etc.) — the tool adds it."
    )

    tcol1, tcol2 = st.columns(2)
    with tcol1:
        list_slug = st.text_input(
            "List *",
            placeholder="e.g. dagster-2025-q1-eng-leaders",
            help="Becomes `list:<value>`. Required.",
        )
        persona = st.text_input(
            "Persona",
            placeholder="e.g. eng-leader",
            help="Becomes `persona:<value>`.",
        )
        segment = st.text_input(
            "Segment",
            placeholder="e.g. data-platform",
            help="Becomes `segment:<value>`. Use for one tab of a multi-tab sheet.",
        )

    with tcol2:
        themes = st.text_input(
            "Theme(s)",
            placeholder="e.g. tech-forward, high-growth",
            help="Comma-separated. Each becomes `theme:<value>`.",
        )
        vertical = st.text_input(
            "Vertical",
            placeholder="e.g. fintech",
            help="Becomes `vertical:<value>`.",
        )
        custom_tags = st.text_area(
            "Custom tags (one per line)",
            placeholder="e.g. campaign:warm-intro\nsource:dagster-reuse",
            help="Pre-formatted with prefix. One per line.",
            height=80,
        )

    preview_tags = build_tags(list_slug, persona, themes, vertical, segment, custom_tags)
    if preview_tags:
        st.caption("**Tags that will be applied:**")
        st.code(", ".join(preview_tags))

    # ---- 4. Column mapping ----
    st.header("4. Column mapping")
    st.caption(
        "Map each Supabase column to a column from your CSV. "
        "Required fields are marked with *. The tool tries to auto-detect matches."
    )

    mapping = {}
    map_cols = st.columns(2)
    for i, col in enumerate(LEAD_COLUMNS):
        target = map_cols[i % 2]
        label = col["label"] + (" *" if col["required"] else "")
        options = ["— skip —"] + csv_columns

        # auto-detect default
        detected = auto_detect_match(col["name"], csv_columns)
        default_idx = options.index(detected) if detected in options else 0

        mapping[col["name"]] = target.selectbox(
            label,
            options=options,
            index=default_idx,
            key=f"map_{col['name']}",
        )

    # ---- 5. Validate ----
    st.header("5. Validate")
    errors = []
    if not client:
        errors.append("Client is required.")
    if not list_slug:
        errors.append("List tag is required.")
    if mapping.get("email") == "— skip —":
        errors.append("Email column must be mapped.")

    if errors:
        for e in errors:
            st.error(e)
        return

    # Build all rows
    def build_row(csv_row) -> dict:
        row = {
            "client": client,
            "tags": preview_tags,
            "source_file": uploaded.name,
            "source_sheet": source_sheet or None,
        }
        for col in LEAD_COLUMNS:
            csv_col = mapping[col["name"]]
            if csv_col == "— skip —":
                row[col["name"]] = None
            else:
                row[col["name"]] = safe_value(csv_row[csv_col], col.get("type"))
        return row

    all_rows = [build_row(df.iloc[i]) for i in range(len(df))]
    valid_rows = [r for r in all_rows if r.get("email")]
    invalid_count = len(all_rows) - len(valid_rows)

    # In-batch dedupe on (client, email) — Supabase upsert can't handle dupes within a single batch
    seen = set()
    deduped_rows = []
    for r in valid_rows:
        key = (r["client"], r["email"].lower())
        if key not in seen:
            seen.add(key)
            r["email"] = r["email"].lower().strip()
            deduped_rows.append(r)
    in_batch_dupes = len(valid_rows) - len(deduped_rows)

    summary_cols = st.columns(3)
    summary_cols[0].metric("Ready to upload", f"{len(deduped_rows):,}")
    summary_cols[1].metric("Skipped (no email)", f"{invalid_count:,}")
    summary_cols[2].metric("In-batch duplicates", f"{in_batch_dupes:,}")

    with st.expander("Preview first 3 rows as they will land in Supabase"):
        st.json(deduped_rows[:3])

    # ---- 6. Upload ----
    st.header("6. Upload to Supabase")
    if not SUPABASE_URL or not SUPABASE_KEY:
        st.error(
            "Supabase credentials not configured. "
            "Set `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` env vars on Railway."
        )
        return

    if st.button("🚀 Push to Supabase", type="primary"):
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
                    batch, on_conflict="client,email"
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


# ============================================================
# RUN
# ============================================================
if check_password():
    main()
