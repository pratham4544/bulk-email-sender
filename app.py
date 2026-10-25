import streamlit as st
import pandas as pd
import smtplib
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from contacts import DEFAULT_FALLBACKS, PLACEHOLDERS, compose, is_excluded_company, parse_contacts
from mailer import Scheduler, deliver

st.set_page_config(
    page_title="Bulk Email Sender",
    page_icon=":mail:",
    layout="centered",
    initial_sidebar_state="collapsed",
)

LINKEDIN_URL = "https://www.linkedin.com/in/prathameshshete/"

BASE_DIR = Path(__file__).parent
TEMPLATE_CSV = BASE_DIR / "templates" / "contacts_template.csv"
TEMPLATE_BODY = BASE_DIR / "templates" / "email_template.txt"
FIELDS = ["Email", "Contact Person", "Company", "Role / Job"]
CUSTOM_FIELDS = ["Subject", "Message"]  # optional per-row overrides


@st.cache_resource
def get_scheduler():
    """One scheduler shared by all sessions, so jobs survive closing the browser tab."""
    return Scheduler()


def user_timezone():
    try:
        return ZoneInfo(st.context.timezone)
    except Exception:  # unknown on the very first run; fall back to the server's zone
        return datetime.now().astimezone().tzinfo


def send_bulk_emails(sender, password, subject, body, contacts, attachments, fallbacks, delay):
    progress = st.progress(0.0, text="Starting...")

    def on_progress(i, total, email):
        progress.progress(i / total, text=f"{i}/{total} processed - last: {email}")

    try:
        return deliver(sender, password, subject, body, contacts, attachments, fallbacks, delay, on_progress)
    except smtplib.SMTPAuthenticationError:
        st.error("Gmail rejected the login. Check the sender email and that you used a 16-character App Password.")
        return None


@st.fragment(run_every=5)
def scheduled_jobs(sender):
    jobs = get_scheduler().jobs_for(sender) if sender else []
    if not jobs:
        return
    tz = user_timezone()
    with st.container(border=True):
        st.markdown("**Scheduled sends**")
        for job in jobs:
            when = job["send_at"].astimezone(tz).strftime("%a %d %b, %H:%M")
            with st.container(horizontal=True, vertical_alignment="center"):
                if job["status"] == "Waiting":
                    st.markdown(f":material/schedule: {job['total']} emails at **{when}**, waiting", width="stretch")
                elif job["status"] == "Sending":
                    st.progress(job["done"] / job["total"], text=f"Sending {job['done']}/{job['total']} (scheduled {when})",
                                width="stretch")
                elif job["status"] == "Done":
                    sent = sum(r["Status"] == "Sent" for r in job["results"])
                    st.markdown(f":material/check_circle: {when}: {sent} sent, {len(job['results']) - sent} failed",
                                width="stretch")
                else:
                    detail = f": {job['error']}" if job["error"] else ""
                    st.markdown(f":material/cancel: {when}: {job['status'].lower()}{detail}", width="stretch")

                if job["status"] in ("Waiting", "Sending"):
                    if st.button("Cancel", key=f"cancel_{job['id']}", icon=":material/close:"):
                        get_scheduler().cancel(job["id"])
                        st.rerun()
                elif job["results"]:
                    st.download_button("Report", pd.DataFrame(job["results"]).to_csv(index=False).encode(),
                                       file_name=f"send_report_{job['id']}.csv", mime="text/csv",
                                       key=f"report_{job['id']}", icon=":material/download:")


def to_contacts(edited_df, extra_cols):
    """Turn the (possibly user-edited) table back into contact dicts."""
    contacts = []
    for row in edited_df.fillna("").to_dict("records"):
        if not row.get("Send"):
            continue
        contact = {f: str(row.get(f, "")).strip() for f in FIELDS + CUSTOM_FIELDS}
        name = contact["Contact Person"]
        contact["First Name"] = name.split()[0] if name else ""
        contact["extras"] = {c: str(row.get(c, "")).strip() for c in extra_cols}
        contacts.append(contact)
    return contacts


def saved_secret(name):
    """Read an optional value from .streamlit/secrets.toml without failing when it's absent."""
    try:
        return st.secrets.get(name, "")
    except Exception:
        return ""


def read_attachments(files):
    attachment_data = []
    for attachment in files or []:
        mime = attachment.type or "application/octet-stream"
        maintype, subtype = mime.split('/', 1)
        attachment_data.append({
            "filename": attachment.name,
            "content": attachment.getvalue(),
            "maintype": maintype,
            "subtype": subtype
        })
    return attachment_data


def main():
    with st.container(horizontal=True, vertical_alignment="center"):
        st.title(":material/forward_to_inbox: Bulk email sender", width="stretch")
        st.link_button("LinkedIn", LINKEDIN_URL, icon=":material/person:")
    st.caption("Fill the two boxes, upload your contacts and resume, then press **Send**. Everything else has sensible defaults.")

    # ---------- 1. Account ----------
    with st.container(border=True):
        st.markdown("**1. Your Gmail account**")
        with st.container(horizontal=True):
            sender = st.text_input("Gmail address", value=saved_secret("sender_email"),
                                   placeholder="you@gmail.com")
            password = st.text_input("App password", value=saved_secret("app_password"), type="password",
                                     placeholder="16 characters, spaces are fine").replace(" ", "")
        st.page_link("pages/app_passwords.py", label="Not your normal password. How to get an app password",
                     icon=":material/help:")

    scheduled_jobs(sender)

    # ---------- 2. Contacts & resume ----------
    with st.container(border=True):
        st.markdown("**2. Contacts and resume**")
        with st.container(horizontal=True):
            csv_file = st.file_uploader("Contacts CSV", type=["csv"])
            attachments = st.file_uploader("Resume / attachments", accept_multiple_files=True)
        with st.container(horizontal=True, vertical_alignment="bottom"):
            excluded_company = st.text_input(
                "Skip your current company", value=saved_secret("current_company"), placeholder="e.g. Strelema",
                help="Contacts whose Company or email domain matches are not emailed. Separate several with commas.")
            st.download_button("Template CSV", data=TEMPLATE_CSV.read_bytes(), file_name="contacts_template.csv",
                               mime="text/csv", icon=":material/download:",
                               help="Columns: Email (required), Contact Person, Company, Role / Job. Optional: Subject and Message to write a custom email for that row. Blank cells are fine.")

    contacts, extra_cols = [], []
    if csv_file:
        try:
            parsed, skipped, extra_cols = parse_contacts(csv_file.getvalue())
        except ValueError as e:
            st.error(str(e))
            parsed, skipped = [], []

        if excluded_company.strip():
            kept = []
            for c in parsed:
                if is_excluded_company(c, excluded_company):
                    skipped.append({"Line": None, "Email": c["Email"], "Reason": "Your current company"})
                else:
                    kept.append(c)
            parsed = kept

        if parsed:
            with_fallback = sum(bool(c["missing"]) for c in parsed)
            with st.expander(f"Review recipients: {len(parsed)} to send, {len(skipped)} skipped, "
                             f"{with_fallback} use a fallback", icon=":material/group:"):
                st.caption("Edit any cell or untick **Send** to exclude a row. Blank cells use the fallback values.")
                shown = FIELDS + [f for f in CUSTOM_FIELDS if any(c[f] for c in parsed)]
                table = pd.DataFrame([
                    {"Send": True, **{f: c[f] for f in shown}, **c["extras"],
                     "Blank → fallback": ", ".join(sorted(c["missing"]))}
                    for c in parsed
                ])
                edited = st.data_editor(table, hide_index=True, disabled=["Blank → fallback"],
                                        column_config={"Message": st.column_config.TextColumn(width="large")},
                                        key=f"editor_{csv_file.file_id}_{excluded_company}")
                contacts = to_contacts(edited, extra_cols)
                if skipped:
                    st.markdown("**Skipped** (invalid, duplicate, or your current company)")
                    st.dataframe(pd.DataFrame(skipped), hide_index=True)

    # ---------- Optional: email text & settings ----------
    with st.expander("Edit email text", icon=":material/edit:"):
        placeholders = list(PLACEHOLDERS) + [f"[{c}]" for c in extra_cols]
        st.caption("Placeholders: " + "  ".join(f"`{p}`" for p in placeholders))
        subject = st.text_input("Subject", value="Application for [Role]")
        body_message = st.text_area("Body", value=TEMPLATE_BODY.read_text(encoding="utf-8"), height=400)

    with st.expander("Settings", icon=":material/tune:"):
        st.markdown("**Used when a cell is blank**")
        fallbacks = {
            "Contact Person": st.text_input("Contact Person", DEFAULT_FALLBACKS["Contact Person"]),
            "Company": st.text_input("Company", DEFAULT_FALLBACKS["Company"]),
            "Role / Job": st.text_input("Role / Job", DEFAULT_FALLBACKS["Role / Job"]),
        }
        fallbacks["First Name"] = fallbacks["Contact Person"]
        for col in extra_cols:
            fallbacks[col] = st.text_input(col, "")
        delay = st.slider("Seconds between emails", 0, 30, 3,
                          help="A small delay lowers the chance of Gmail flagging the batch. "
                               "Gmail allows roughly 500 emails/day from a personal account.")

    # ---------- 3. Preview & send ----------
    with st.container(border=True):
        st.markdown("**3. Preview and send**")
        if not contacts:
            st.info("Upload a contacts CSV to see a preview.", icon=":material/upload_file:")
            return

        pick = st.selectbox("Preview for", range(len(contacts)), label_visibility="collapsed",
                            format_func=lambda i: f"Preview: {contacts[i]['Email']} ({contacts[i]['Contact Person'] or 'no name'})")
        preview_subject, preview_body = compose(contacts[pick], subject, body_message, fallbacks)
        own = [f.lower() for f in CUSTOM_FIELDS if contacts[pick][f]]
        st.markdown(f"**Subject:** {preview_subject}"
                    + (f"  :blue-badge[Own {' and '.join(own)} from CSV]" if own else ""))
        with st.container(height=250):
            st.text(preview_body)
        st.caption("Attachments: " + (", ".join(a.name for a in attachments) if attachments else "none"))

        when = st.segmented_control("When", ["Send now", "Schedule"], default="Send now", required=True,
                                    label_visibility="collapsed")
        send_at = None
        if when == "Schedule":
            tz = user_timezone()
            tomorrow = datetime.now(tz).date() + timedelta(days=1)
            with st.container(horizontal=True):
                day = st.date_input("Date", value=tomorrow, min_value=datetime.now(tz).date())
                at_time = st.time_input("Time", value=time(10, 0), step=timedelta(minutes=15))
            send_at = datetime.combine(day, at_time, tzinfo=tz)
            st.caption(f"Times are in your timezone ({tz}). Keep the app running until then: "
                       "on your own computer leave the terminal open and the laptop awake.")

        missing = [label for label, val in (("Gmail address", sender), ("app password", password)) if not val]
        label = (f"Schedule {len(contacts)} emails for {send_at:%a %d %b, %H:%M}" if send_at
                 else f"Send {len(contacts)} emails")
        with st.container(horizontal=True):
            send_clicked = st.button(label, type="primary", icon=":material/schedule_send:" if send_at else ":material/send:",
                                     disabled=bool(missing), width="stretch")
            test_clicked = st.button("Test to myself", icon=":material/science:", disabled=bool(missing),
                                     help="Sends the email shown above to your own Gmail.")
        if missing:
            st.caption("Enter your " + " and ".join(missing) + " in step 1 to send.")

    if not (test_clicked or send_clicked):
        return

    attachment_data = read_attachments(attachments)
    if test_clicked:
        # Uses the previewed contact's details but delivers to the sender.
        test_contact = {**contacts[pick], "Email": sender,
                        "Subject": "[TEST] " + preview_subject, "Message": preview_body}
        results = send_bulk_emails(sender, password, subject, body_message, [test_contact],
                                   attachment_data, fallbacks, 0)
        if results and results[0]["Status"] == "Sent":
            st.success(f"Test email sent to {sender}. Check your inbox.")
        elif results:
            st.error(f"Test failed: {results[0]['Error']}")
        return

    if send_at:
        if send_at <= datetime.now(timezone.utc):
            st.error("That time has already passed. Pick a time in the future.")
            return
        get_scheduler().schedule(send_at.astimezone(timezone.utc), sender, password, subject, body_message,
                                 contacts, attachment_data, fallbacks, delay)
        st.success(f"Scheduled {len(contacts)} emails for {send_at:%a %d %b, %H:%M}. "
                   "You can close this tab; progress shows under step 1.")
        return

    results = send_bulk_emails(sender, password, subject, body_message, contacts,
                               attachment_data, fallbacks, delay)
    if results is None:
        return
    sent = sum(r["Status"] == "Sent" for r in results)
    failed = len(results) - sent
    (st.success if not failed else st.warning)(f"Done: {sent} sent, {failed} failed.")
    results_df = pd.DataFrame(results)
    st.dataframe(results_df, hide_index=True)
    st.download_button("Download send report", results_df.to_csv(index=False).encode(),
                       file_name="send_report.csv", mime="text/csv", icon=":material/download:")


if __name__ == "__main__":
    main()
