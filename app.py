"""
Lead Uploader — internal Founderled tool.

Reads a CSV, lets you map columns to the Supabase `leads` schema,
configure tags as key/value inputs, preview, and upsert in batches.
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
    {"name": "number_of_connections", "label": "Connections", "required": False, "type": "int"},
    {"name": "mx_records", "label": "MX records", "required": False, "type": "array"},
]

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
# STYLES — adapt to both light and dark mode
# ============================================================
COMPACT_CSS = """
<style>
.block-container {
    padding-top: 2rem;
    padding-bottom: 3rem;
    max-width: 900px;
}
h2, h3 {
    margin-top: 1.5rem !important;
    margin-bottom: 0.5rem !important;
}
h2 { font-size: 1.35rem !important; }
h3 { font-size: 1.1rem !important; }
div[data-baseweb="select"] > div {
    min-height: 36px !important;
}
.stTextInput input {
    padding: 0.4rem 0.75rem !important;
}
.stTextArea textarea {
    padding: 0.5rem 0.75rem !important;
}
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


def auto_detect_match(supabase_col: str, csv_columns: list):
    aliases = set(COLUMN_ALIASES.get(supabase_col, []))
    aliases.add(normalize_header(supabase_col))
    for csv_col in csv_columns:
        if normalize_header(csv_col) in aliases:
            return csv_col
    return None


def build_tags(list_slug, persona, themes_str, vertical, segment, custom_tags):
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
                tags.append(line)
    seen = set()
    out = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def safe_value(val, col_type=None):
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    s = str(val).strip()
    if s == "" or s.lower() == "nan":
        return None
    if col_type == "int":
        try:
            return int(float(s.replace(",", "")))
        except (ValueError, TypeError):
            return None
    if col_type == "array":
        return [x.strip() for x in s.split(",") if x.strip()]
    return s


# ============================================================
# MAIN
# ============================================================
def main():
    st.markdown(COMPACT_CSS, unsafe_allow_html=True)

    st.title("📥 Lead Uploader")
    st.caption(
        "Upload a CSV → set tags → map columns → push to Supabase. "
        "Toggle light/dark mode from the menu (≡) in the top-right."
    )

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

    # ---- 2. Client + tags ----
    st.subheader("2. Client & tags")
    st.caption("These are applied to every row in this upload.")

    # Client
    c1, c2 = st.columns([1, 2], vertical_alignment="center")
    with c1:
        st.markdown("**Client** <span style='color:#ef4444;'>＊</span>", unsafe_allow_html=True)
    with c2:
        client_choice = st.selectbox(
            "Client",
            options=KNOWN_CLIENTS + ["+ Add new client"],
            label_visibility="collapsed",
        )
    if client_choice == "+ Add new client":
        c1, c2 = st.columns([1, 2], vertical_alignment="center")
        with c1:
            st.markdown("**New client slug**")
        with c2:
            new_client = st.text_input(
                "new_client", placeholder="e.g. acme-corp", label_visibility="collapsed"
            )
            client = slugify(new_client) if new_client else ""
    else:
        client = client_choice

    # Source sheet/tab name (optional — only relevant if this CSV came from one tab of a multi-tab Sheet)
    c1, c2 = st.columns([1, 2], vertical_alignment="center")
    with c1:
        st.markdown("**Source sheet**")
    with c2:
        source_sheet = st.text_input(
            "source_sheet",
            placeholder="Optional — original Google Sheet tab name, e.g. Engineering",
            label_visibility="collapsed",
            help="Only fill in if this CSV is one tab from a multi-tab Google Sheet. Leave blank otherwise.",
        )

    # Tag inputs (label-on-left layout)
    def tag_row(label, key, placeholder, required=False):
        c1, c2 = st.columns([1, 2], vertical_alignment="center")
        with c1:
            marker = " <span style='color:#ef4444;'>＊</span>" if required else ""
            st.markdown(f"**{label}**{marker}", unsafe_allow_html=True)
        with c2:
            return st.text_input(
                label=label,
                key=key,
                placeholder=placeholder,
                label_visibility="collapsed",
            )

    list_slug = tag_row("List", "tag_list", "e.g. dagster-2025-q1-eng-leaders", required=True)
    persona = tag_row("Persona", "tag_persona", "e.g. eng-leader")
    themes = tag_row("Theme(s)", "tag_themes", "e.g. tech-forward, high-growth (comma-separated)")
    vertical = tag_row("Vertical", "tag_vertical", "e.g. fintech")
    segment = tag_row("Segment", "tag_segment", "e.g. data-platform")

    # Custom tags
    c1, c2 = st.columns([1, 2], vertical_alignment="top")
    with c1:
        st.markdown("**Custom tags**")
        st.caption("One per line, with prefix.")
    with c2:
        custom_tags = st.text_area(
            "custom_tags",
            placeholder="campaign:warm-intro\nsource:dagster-reuse",
            height=70,
            label_visibility="collapsed",
        )

    preview_tags = build_tags(list_slug, persona, themes, vertical, segment, custom_tags)
    if preview_tags:
        st.caption("**Tags that will be applied:**")
        st.code(", ".join(preview_tags), language=None)

    # ---- 3. Column mapping ----
    st.subheader("3. Column mapping")
    st.caption(
        "Supabase columns on the left, CSV columns on the right. "
        "Auto-detected matches are pre-selected — override anything that looks wrong."
    )

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

    # ---- 4. Validate ----
    errors = []
    if not client:
        errors.append("Client is required.")
    if not list_slug:
        errors.append("List tag is required.")
    if mapping.get("email") == "— skip —":
        errors.append("Email column must be mapped.")

    if errors:
        st.subheader("4. Issues to fix")
        for e in errors:
            st.error(e)
        return

    # Build rows
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

    seen = set()
    deduped_rows = []
    for r in valid_rows:
        key = (r["client"], r["email"].lower())
        if key not in seen:
            seen.add(key)
            r["email"] = r["email"].lower().strip()
            deduped_rows.append(r)
    in_batch_dupes = len(valid_rows) - len(deduped_rows)

    st.subheader("4. Review")
    m1, m2, m3 = st.columns(3)
    m1.metric("Ready to upload", f"{len(deduped_rows):,}")
    m2.metric("Skipped (no email)", f"{invalid_count:,}")
    m3.metric("In-batch duplicates", f"{in_batch_dupes:,}")

    with st.expander("Preview first 3 rows as they will land in Supabase"):
        st.json(deduped_rows[:3])

    # ---- 5. Upload ----
    st.subheader("5. Upload")
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
main()
