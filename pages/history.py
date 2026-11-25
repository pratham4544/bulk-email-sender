"""Every email sent from the signed-in profile, filterable by age."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import profile_ui
import profiles

st.set_page_config(page_title="Send history", page_icon=":material/history:", layout="wide")
st.title(":material/history: Send history")
username = profile_ui.require_login()
uri = profile_ui.mongo_uri()

try:
    tz = ZoneInfo(st.context.timezone)
except Exception:
    tz = datetime.now().astimezone().tzinfo

try:
    rows = profiles.history(uri, username)
except Exception as e:
    st.error(f"Could not load the history: {e}")
    st.stop()

if not rows:
    st.info("Nothing sent yet. Emails you send while logged in show up here.", icon=":material/inbox:")
    st.page_link("app.py", label="Go to the main page", icon=":material/home:")
    st.stop()

now = datetime.now(timezone.utc)
age = lambda r: now - r["sent_at"]
sent_rows = [r for r in rows if r["status"] == "Sent"]
with st.container(horizontal=True):
    st.metric("Sent, all time", len(sent_rows), border=True)
    for days in (7, 15, 30):
        st.metric(f"Last {days} days", sum(age(r) < timedelta(days=days) for r in sent_rows), border=True)
    st.metric("Failed", len(rows) - len(sent_rows), border=True)

RANGES = {
    "All": lambda r: True,
    "Last 7 days": lambda r: age(r) < timedelta(days=7),
    "Last 15 days": lambda r: age(r) < timedelta(days=15),
    "Last 30 days": lambda r: age(r) < timedelta(days=30),
    "Older than 7 days": lambda r: age(r) >= timedelta(days=7),
    "Older than 15 days": lambda r: age(r) >= timedelta(days=15),
    "Older than 30 days": lambda r: age(r) >= timedelta(days=30),
}
with st.container(horizontal=True, vertical_alignment="bottom"):
    when = st.selectbox("Show", list(RANGES), key="history_range")
    status = st.segmented_control("Status", ["All", "Sent", "Failed"], default="All", required=True,
                                  key="history_status")
    query = st.text_input("Search", placeholder="Email, name, company, role or subject", key="history_query")

q = query.strip().lower()
shown = [r for r in rows if RANGES[when](r)
         and (status == "All" or r["status"] == status)
         and (not q or any(q in str(r.get(f, "")).lower() for f in ("email", "contact", "company", "role", "subject")))]

table = pd.DataFrame([{
    "Sent": r["sent_at"].astimezone(tz).strftime("%d %b %Y, %H:%M"),
    "Days ago": age(r).days,
    "Email": r["email"], "Contact": r["contact"], "Company": r["company"], "Role": r["role"],
    "Subject": r["subject"], "Status": r["status"],
    "Follow-ups": r.get("followups", 0), "Replied": "Yes" if r.get("replied") else "",
    "Error": r["error"],
} for r in shown])

st.caption(f"{len(shown)} of {len(rows)} emails")
if shown:
    st.dataframe(table, hide_index=True, column_config={"Subject": st.column_config.TextColumn(width="large")})
    st.download_button("Download as CSV", table.to_csv(index=False).encode(), file_name="send_history.csv",
                       mime="text/csv", icon=":material/download:")
