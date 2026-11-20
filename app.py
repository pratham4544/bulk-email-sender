import streamlit as st
import pandas as pd
import smtplib
from datetime import datetime, time, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from zoneinfo import ZoneInfo

import automation as auto
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


def background_text(files):
    """Text of the first readable resume among the uploads, used to personalise emails."""
    for f in files or []:
        try:
            return auto.resume_text(f.name, f.getvalue())
        except Exception:
            continue
    return ""


def find_contacts(apify_token, gemini_key, queries, max_posts, sort_label, posted_label, note,
                  background_files):
    """Scrape posts, let Gemini write the emails, and keep the result as a CSV in session state."""
    status = st.status("Starting the LinkedIn scraper…", expanded=True)
    try:
        posts = auto.scrape_linkedin_posts(
            apify_token, queries, max_posts, auto.SORT_OPTIONS[sort_label], auto.POSTED_OPTIONS[posted_label],
            on_status=lambda m: status.update(label=m))
        with_email = auto.posts_with_emails(posts)
        status.write(f"{len(posts)} posts found, {len(with_email)} contain an email address.")
        if not with_email:
            status.update(label="No posts with an email address. Try other keywords.", state="error")
            return
        rows, errors = auto.build_contacts(
            gemini_key, st.session_state.get("gemini_model") or auto.DEFAULT_GEMINI_MODEL, with_email,
            background_text(background_files), st.session_state.get("body_text", TEMPLATE_BODY.read_text()),
            note, on_status=lambda m: status.update(label=m))
    except (auto.ApifyError, auto.GeminiError, OSError) as e:
        status.update(label=str(e), state="error")
        return
    for err in errors:
        status.write(f":orange[{err}]")
    if not rows:
        status.update(label="Gemini could not write any emails.", state="error")
        return
    st.session_state.auto_csv = auto.rows_to_csv(rows)
    st.session_state.auto_run = st.session_state.get("auto_run", 0) + 1
    status.update(label=f"Ready: {len(rows)} contacts with personalised emails.", state="complete",
                  expanded=False)


def linkedin_finder(apify_token, gemini_key, background_files):
    """Step 2 in automatic mode: scrape posts, write the emails, keep the CSV in session state."""
    keywords = st.text_area("Search keywords (one search per line)", key="li_keywords", height=90,
                            placeholder='"hiring" AND "GenAI engineer"\nAI ML engineer hiring email')
    with st.container(horizontal=True):
        max_posts = st.number_input("Max posts per search", 1, 500, 50, step=10, key="li_max")
        sort_label = st.selectbox("Sort by", list(auto.SORT_OPTIONS), key="li_sort")
        posted_label = st.selectbox("Posted", list(auto.POSTED_OPTIONS), index=2, key="li_posted")
    note = st.text_area("Note for the AI (optional)", key="li_note", height=70,
                        placeholder="e.g. Mention I can join in 30 days. Keep it under 120 words.",
                        help="Added to the instructions Gemini uses to write each subject and email.")

    queries = [q.strip() for q in keywords.splitlines() if q.strip()]
    need = [n for n, v in (("Apify token", apify_token), ("Gemini API key", gemini_key)) if not v]
    clicked = st.button("Find contacts and write emails", icon=":material/travel_explore:", type="primary",
                        disabled=bool(need or not queries))
    if need:
        st.caption("Enter your " + " and ".join(need) + " in step 1.")
    elif not queries:
        st.caption("Add at least one search keyword.")

    if clicked:
        find_contacts(apify_token, gemini_key, queries, max_posts, sort_label, posted_label, note,
                      background_files)

    if st.session_state.get("auto_csv"):
        st.download_button("Download generated CSV", st.session_state.auto_csv, file_name="linkedin_contacts.csv",
                           mime="text/csv", icon=":material/download:",
                           help="Edit it in a spreadsheet and switch automation off to upload it manually later.")
        return st.session_state.auto_csv, f"auto_{st.session_state.auto_run}"
    return None, None


def job_description(contact):
    jd = contact["extras"].get("Job Description", "")
    if jd:
        return jd
    if contact["Role / Job"]:
        return f"Role: {contact['Role / Job']}" + (f" at {contact['Company']}" if contact["Company"] else "")
    return ""


def tailoring_key(base, contact, note):
    return (base.file_id, job_description(contact), contact["Company"], note.strip())


def tailor_resumes(gemini_key, base, base_text, contacts, note):
    """Generate a tailored PDF for every contact that has a job description and none cached yet."""
    cache = st.session_state.setdefault("tailored", {})
    model = st.session_state.get("gemini_model") or auto.DEFAULT_GEMINI_MODEL
    todo = [c for c in contacts if job_description(c) and tailoring_key(base, c, note) not in cache]
    progress = st.progress(0.0, text="Tailoring resumes…")
    failed = []

    def one(contact):
        data = auto.tailor_resume(gemini_key, model, base_text, job_description(contact), note)
        return contact, auto.resume_filename(data, contact["Company"]), auto.resume_pdf(data)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(one, c) for c in todo]
        for i, fut in enumerate(as_completed(futures), start=1):
            try:
                contact, name, pdf = fut.result()
                cache[tailoring_key(base, contact, note)] = (name, pdf)
            except Exception as e:
                failed.append(str(e))
            progress.progress(i / len(todo), text=f"Tailored {i}/{len(todo)}")
    progress.empty()
    if failed:
        st.warning(f"{len(failed)} resumes failed: {failed[0]}")


def main():
    with st.container(horizontal=True, vertical_alignment="center"):
        st.title(":material/forward_to_inbox: Bulk email sender", width="stretch")
        st.link_button("LinkedIn", LINKEDIN_URL, icon=":material/person:")
    with st.container(horizontal=True):
        automatic = st.toggle("Find contacts on LinkedIn", key="auto_mode",
                              help="Off: upload your own CSV. On: search LinkedIn posts with Apify and let Gemini "
                                   "write a personal subject and email for every address found.")
        tailor = st.toggle("Tailor resume to each job", key="tailor_mode",
                           help="Gemini rewrites your resume for each job description and attaches that PDF "
                                "instead of your base resume. Uses only facts already in your resume.")
    st.caption("Fill the boxes, add your contacts and resume, then press **Send**. Everything else has sensible defaults.")

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
        apify_token = gemini_key = ""
        if automatic or tailor:
            with st.container(horizontal=True):
                if automatic:
                    apify_token = st.text_input("Apify API token", value=saved_secret("apify_token"),
                                                type="password", help="Apify console → Settings → API & Integrations.")
                gemini_key = st.text_input("Gemini API key", value=saved_secret("gemini_api_key"), type="password",
                                           help="Free key at aistudio.google.com/apikey.")

    scheduled_jobs(sender)

    # ---------- 2. Contacts & resume ----------
    with st.container(border=True):
        st.markdown("**2. Contacts and resume**" if not automatic else "**2. Find contacts on LinkedIn**")
        csv_file = None
        with st.container(horizontal=True):
            if not automatic:
                csv_file = st.file_uploader("Contacts CSV", type=["csv"])
            attachments = st.file_uploader("Other attachments" if tailor else "Resume / attachments",
                                           accept_multiple_files=True,
                                           help="Your base resume goes in step 3 while tailoring is on." if tailor else None)
            base_resume = None
            if tailor:
                base_resume = st.file_uploader("Base resume (PDF, DOCX or TXT)", type=["pdf", "docx", "txt"],
                                               key="base_resume")
        with st.container(horizontal=True, vertical_alignment="bottom"):
            excluded_company = st.text_input(
                "Skip your current company", value=saved_secret("current_company"), placeholder="e.g. Strelema",
                help="Contacts whose Company or email domain matches are not emailed. Separate several with commas.")
            st.download_button("Template CSV", data=TEMPLATE_CSV.read_bytes(), file_name="contacts_template.csv",
                               mime="text/csv", icon=":material/download:",
                               help="Columns: Email (required), Contact Person, Company, Role / Job. Optional: Subject and Message to write a custom email for that row. Blank cells are fine.")

        csv_bytes, csv_key = (csv_file.getvalue(), csv_file.file_id) if csv_file else (None, None)
        if automatic:
            csv_bytes, csv_key = linkedin_finder(apify_token, gemini_key,
                                                 [base_resume] + list(attachments or []) if base_resume else attachments)

    contacts, extra_cols = [], []
    if csv_bytes:
        try:
            parsed, skipped, extra_cols = parse_contacts(csv_bytes)
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
                                        column_config={"Message": st.column_config.TextColumn(width="large"),
                                                       "Job Description": st.column_config.TextColumn(width="large"),
                                                       "LinkedIn Post": st.column_config.LinkColumn(display_text="Open post")},
                                        key=f"editor_{csv_key}_{excluded_company}")
                contacts = to_contacts(edited, extra_cols)
                if skipped:
                    st.markdown("**Skipped** (invalid, duplicate, or your current company)")
                    st.dataframe(pd.DataFrame(skipped), hide_index=True)

    # ---------- Optional: email text & settings ----------
    with st.expander("Edit email text", icon=":material/edit:"):
        placeholders = list(PLACEHOLDERS) + [f"[{c}]" for c in extra_cols]
        st.caption("Placeholders: " + "  ".join(f"`{p}`" for p in placeholders))
        subject = st.text_input("Subject", value="Application for [Role]")
        body_message = st.text_area("Body", value=TEMPLATE_BODY.read_text(encoding="utf-8"), height=400,
                                     key="body_text")

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
        if automatic or tailor:
            st.selectbox("Gemini model", list(auto.GEMINI_MODELS), key="gemini_model", accept_new_options=True,
                         format_func=lambda m: f"{m}: {auto.GEMINI_MODELS[m]}" if m in auto.GEMINI_MODELS else m,
                         help="Used for writing emails and tailoring resumes. You can also type any other model id.")
        delay = st.slider("Seconds between emails", 0, 30, 3,
                          help="A small delay lowers the chance of Gmail flagging the batch. "
                               "Gmail allows roughly 500 emails/day from a personal account.")

    # ---------- Optional: tailored resumes ----------
    attachment_data = read_attachments(attachments)
    if tailor:
        with st.container(border=True):
            st.markdown("**3. Tailored resumes**")
            tailor_note = st.text_input("Note for the AI (optional)", key="tailor_note",
                                        placeholder="e.g. Keep it to one page, put Projects before Experience")
            if not base_resume:
                st.info("Upload your base resume in step 2.", icon=":material/description:")
            elif contacts:
                try:
                    base_text = auto.resume_text(base_resume.name, base_resume.getvalue())
                except Exception as e:
                    st.error(f"Could not read the resume: {e}")
                    base_text = ""
                cache = st.session_state.get("tailored", {})
                with_jd = [c for c in contacts if job_description(c)]
                done = sum(tailoring_key(base_resume, c, tailor_note) in cache for c in with_jd)
                st.caption(f"{done} of {len(with_jd)} resumes tailored. Contacts without a job description or role "
                           f"({len(contacts) - len(with_jd)}) get your base resume. The tailored PDF uses a simple layout, "
                           "not your original design, so check one before sending.")
                if base_text and done < len(with_jd):
                    if st.button(f"Tailor {len(with_jd) - done} resumes", icon=":material/auto_fix_high:",
                                 disabled=not gemini_key):
                        tailor_resumes(gemini_key, base_resume, base_text, with_jd, tailor_note)
                        st.rerun()
                    if not gemini_key:
                        st.caption("Enter your Gemini API key in step 1.")

            # Each contact gets the other files plus either its tailored PDF or the base resume.
            if base_resume:
                base_file = read_attachments([base_resume])[0]
                cache = st.session_state.get("tailored", {})
                for c in contacts:
                    hit = cache.get(tailoring_key(base_resume, c, tailor_note))
                    resume_file = ({"filename": hit[0], "content": hit[1], "maintype": "application",
                                    "subtype": "pdf"} if hit else base_file)
                    c["attachments"] = attachment_data + [resume_file]

    # ---------- Preview & send ----------
    with st.container(border=True):
        st.markdown(f"**{4 if tailor else 3}. Preview and send**")
        if not contacts:
            st.info("Find contacts on LinkedIn above to see a preview." if automatic
                    else "Upload a contacts CSV to see a preview.", icon=":material/upload_file:")
            return

        pick = st.selectbox("Preview for", range(len(contacts)), label_visibility="collapsed",
                            format_func=lambda i: f"Preview: {contacts[i]['Email']} ({contacts[i]['Contact Person'] or 'no name'})")
        preview_subject, preview_body = compose(contacts[pick], subject, body_message, fallbacks)
        own = [f.lower() for f in CUSTOM_FIELDS if contacts[pick][f]]
        st.markdown(f"**Subject:** {preview_subject}"
                    + (f"  :blue-badge[Own {' and '.join(own)} from CSV]" if own else ""))
        with st.container(height=250):
            st.text(preview_body)
        files = contacts[pick].get("attachments", attachment_data)
        st.caption("Attachments: " + (", ".join(a["filename"] for a in files) if files else "none"))
        tailored = [a for a in files if base_resume and a["filename"] != base_resume.name
                    and a not in attachment_data]
        if tailored:
            st.download_button("Check tailored resume", tailored[0]["content"], file_name=tailored[0]["filename"],
                               mime="application/pdf", icon=":material/picture_as_pdf:", key=f"tr_{pick}")

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
