"""Contacts emailed a while ago with no reply yet: send a follow-up in the same thread, or mark them as replied."""
import smtplib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import profile_ui
import profiles
from contacts import DEFAULT_FALLBACKS, PLACEHOLDERS, compose
from mailer import deliver

st.set_page_config(page_title="Follow-ups", page_icon=":material/reply:", layout="wide")
st.title(":material/reply: Follow-ups")
username = profile_ui.require_login()
uri = profile_ui.mongo_uri()
saved = profile_ui.data()

DEFAULTS = {
    "followup_days": 7,
    "followup_subject": "Re: [Original Subject]",
    "followup_body": ("Hi [First Name],\n\nI wanted to follow up on my email from [Sent Date] about [Role] at [Company]. "
                      "I'm still very interested and would be glad to share anything else that helps.\n\n"
                      "Thank you for your time.\n\nBest regards"),
}
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, saved.get(k, v))
profile_ui.autosave()

try:
    tz = ZoneInfo(st.context.timezone)
except Exception:
    tz = datetime.now().astimezone().tzinfo

days = st.segmented_control("Show contacts with no reply after", [7, 15, 30], key="followup_days", required=True,
                            format_func=lambda d: f"{d} days")
st.caption("Counts from your last email or follow-up to that address. Mark a contact as replied to take it off the list.")

try:
    due = profiles.followups_due(uri, username, days)
except Exception as e:
    st.error(f"Could not load your send history: {e}")
    st.stop()

if not due:
    st.success(f"Nobody is waiting for a follow-up after {days} days.", icon=":material/check_circle:")
    st.stop()

now = datetime.now(timezone.utc)
table = pd.DataFrame([{
    "Select": True,
    "Sent": r["sent_at"].astimezone(tz).strftime("%d %b %Y"),
    "Days ago": (now - (r.get("last_followup_at") or r["sent_at"])).days,
    "Email": r["email"], "Contact": r["contact"], "Company": r["company"], "Role": r["role"],
    "Subject": r["subject"], "Follow-ups": r.get("followups", 0),
} for r in due])
edited = st.data_editor(table, hide_index=True, disabled=[c for c in table.columns if c != "Select"],
                        column_config={"Subject": st.column_config.TextColumn(width="large")},
                        key=f"followup_editor_{days}_{st.session_state.get('followup_rev', 0)}")
picked = [due[i] for i in edited.index[edited["Select"]]]

with st.expander("Follow-up email", icon=":material/edit:"):
    st.caption("Placeholders: " + "  ".join(f"`{p}`" for p in [*PLACEHOLDERS, "[Original Subject]", "[Sent Date]"]))
    st.text_input("Subject", key="followup_subject")
    st.text_area("Body", height=250, key="followup_body")
    attachments = st.file_uploader("Attachments (optional)", accept_multiple_files=True, key="followup_files")


def as_contact(r):
    name = r["contact"]
    return {"Email": r["email"], "Contact Person": name, "First Name": name.split()[0] if name else "",
            "Company": r["company"], "Role / Job": r["role"],
            "Subject": st.session_state.followup_subject, "Message": st.session_state.followup_body,
            "In-Reply-To": r.get("message_id", ""),
            "extras": {"Original Subject": r["subject"], "Sent Date": r["sent_at"].astimezone(tz).strftime("%d %b")}}


fallbacks = {"Contact Person": saved.get("fb_contact", DEFAULT_FALLBACKS["Contact Person"]),
             "Company": saved.get("fb_company", DEFAULT_FALLBACKS["Company"]),
             "Role / Job": saved.get("fb_role", DEFAULT_FALLBACKS["Role / Job"]),
             "Original Subject": "my application"}
fallbacks["First Name"] = fallbacks["Contact Person"]

if picked:
    with st.container(border=True):
        subject, body = compose(as_contact(picked[0]), "", "", fallbacks)
        st.markdown(f"**Preview for {picked[0]['email']}**  \n**Subject:** {subject}")
        st.text(body)

sender, password = username, (saved.get("password") or "").replace(" ", "")
with st.container(horizontal=True):
    send = st.button(f"Send {len(picked)} follow-ups", type="primary", icon=":material/send:",
                     disabled=not (picked and sender and password))
    replied = st.button(f"Mark {len(picked)} as replied", icon=":material/mark_email_read:", disabled=not picked)
if not (sender and password):
    st.caption("Add your Gmail app password in your profile first.")

if replied:
    profiles.mark_replied(uri, username, [r["_id"] for r in picked])
    st.session_state.followup_rev = st.session_state.get("followup_rev", 0) + 1
    st.rerun()

if send:
    files = []
    for f in attachments or []:
        maintype, _, subtype = (f.type or "application/octet-stream").partition("/")
        files.append({"filename": f.name, "content": f.getvalue(), "maintype": maintype, "subtype": subtype})
    progress = st.progress(0.0, text="Starting...")
    try:
        results = deliver(sender, password, "", "", [as_contact(r) for r in picked], files, fallbacks,
                          saved.get("delay", 3),
                          lambda i, n, email: progress.progress(i / n, text=f"{i}/{n} processed - last: {email}"))
    except smtplib.SMTPAuthenticationError:
        st.error("Gmail rejected the login. Check the app password in your profile.")
        st.stop()
    ok = [r["_id"] for r, res in zip(picked, results) if res["Status"] == "Sent"]
    profiles.mark_followed_up(uri, username, ok)
    profiles.record_sends(uri, username, results, kind="followup")
    st.session_state.followup_rev = st.session_state.get("followup_rev", 0) + 1
    failed = [res for res in results if res["Status"] != "Sent"]
    (st.warning if failed else st.success)(f"{len(ok)} follow-ups sent, {len(failed)} failed.")
    if failed:
        st.dataframe(pd.DataFrame(failed)[["Email", "Error"]], hide_index=True)
