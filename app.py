import streamlit as st
import pandas as pd
import hashlib
import smtplib
from datetime import datetime, time, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import automation as auto
import profile_ui
import profiles
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
                    st.download_button("Report", pd.DataFrame(job["results"]).drop(columns=["Message-ID"])
                                       .to_csv(index=False).encode(),
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


def llm_settings(use_groq, api_key):
    """The provider/key/model dict automation.py expects; the model comes from the Settings dropdown."""
    if use_groq:
        return {"provider": "groq", "name": "Groq", "api_key": api_key,
                "model": st.session_state.get("groq_model") or auto.DEFAULT_GROQ_MODEL}
    return {"provider": "gemini", "name": "Gemini", "api_key": api_key,
            "model": st.session_state.get("gemini_model") or auto.DEFAULT_GEMINI_MODEL}


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


class SearchStore:
    """Where LinkedIn searches live: MongoDB for a profile (survives errors, refreshes and restarts),
    session state for a guest (survives errors, not a closed tab)."""

    def __init__(self, uri, username):
        self.uri, self.username = uri, username

    def latest(self):
        if self.username:
            return profiles.latest_search(self.uri, self.username)
        return st.session_state.get("guest_search")

    def new(self, params):
        if self.username:
            return profiles.new_search(self.uri, self.username, params)
        st.session_state.guest_search = {
            "_id": "guest", "params": params, "status": "scraping", "run_id": None, "posts": [], "total_posts": 0,
            "rows": [], "error": "", "created_at": datetime.now(timezone.utc)}
        return "guest"

    def update(self, search_id, **fields):
        if self.username:
            profiles.update_search(self.uri, search_id, **fields)
        else:
            st.session_state.guest_search.update(fields)

    def add_rows(self, search_id, rows):
        if self.username:
            profiles.add_search_rows(self.uri, search_id, rows)
        else:
            st.session_state.guest_search["rows"] += rows

    def get(self, search_id):
        return profiles.get_search(self.uri, search_id) if self.username else st.session_state.get("guest_search")

    def archive(self, search_id):
        if self.username:
            profiles.update_search(self.uri, search_id, archived=True)
        else:
            st.session_state.pop("guest_search", None)


def run_search(store, search_id, apify_token, llm, background, template, note):
    """Run (or continue) a search. Each step is saved as soon as it is done: the Apify run id before
    waiting, the posts once scraped, every batch of emails once written. Nothing paid for is lost."""
    search = store.get(search_id)
    status = st.status("Starting the LinkedIn scraper…", expanded=True)
    try:
        if search["status"] == "scraping":
            run_id = search["run_id"]
            if not run_id:
                p = search["params"]
                run_id = auto.start_scrape(apify_token, p["queries"], p["max_posts"], auto.SORT_OPTIONS[p["sort"]],
                                           auto.POSTED_OPTIONS[p["posted"]])
                store.update(search_id, run_id=run_id)
            else:
                status.update(label="Picking up the search that was already running on Apify…")
            posts = auto.wait_for_scrape(apify_token, run_id, on_status=lambda m: status.update(label=m))
            with_email = [auto.slim_post(x) for x in auto.posts_with_emails(posts)]
            store.update(search_id, posts=with_email, total_posts=len(posts), status="writing")
            status.write(f"{len(posts)} posts found, {len(with_email)} contain an email address.")
            if not with_email:
                store.update(search_id, status="done")
                status.update(label="No posts with an email address. Try other keywords.", state="error")
                return
            search = store.get(search_id)

        done = {r["Email"].lower() for r in search["rows"]}
        todo = [x for x in search["posts"] if not all(e.lower() in done for e in x["_emails"])]
        _, errors = auto.build_contacts(llm, todo, background, template, note,
                                        on_status=lambda m: status.update(label=m),
                                        on_rows=lambda rows: store.add_rows(search_id, rows))
    except (auto.ApifyError, auto.LLMError, OSError, requests.RequestException) as e:
        store.update(search_id, error=str(e))
        status.update(label=f"{e} Nothing is lost: press Continue to pick up where it stopped.", state="error")
        return
    store.update(search_id, status="done", error="; ".join(errors[:2]))
    for err in errors:
        status.write(f":orange[{err}]")
    status.update(label="Done. Check the contacts below.", state="complete", expanded=bool(errors))


def search_rows(search, template_body):
    """Rows the AI wrote, plus (once the search is done) rows with your fallback email for the rest."""
    rows = auto.dedupe(search["rows"])
    fallback = []
    if search["status"] == "done":
        fallback = auto.fallback_rows(search["posts"], template_body, done_emails=[r["Email"] for r in rows])
    return rows, fallback


def linkedin_finder(store, apify_token, llm, background, template_body):
    """Search LinkedIn posts with Apify, let the AI write each email, keep everything in `store`."""
    note = st.session_state.get("li_note", "")
    latest = store.latest()
    if latest and latest["status"] in ("scraping", "writing"):
        written, total = len(auto.dedupe(latest["rows"])), sum(len(p["_emails"]) for p in latest["posts"])
        step = ("while scraping LinkedIn" if latest["status"] == "scraping"
                else f"after writing {written} of {total} emails")
        with st.container(border=True):
            st.markdown(f":material/history: Your last search stopped {step}"
                        + (f": {latest['error']}" if latest["error"] else "."))
            st.caption("Nothing is lost. Continue picks up where it stopped without paying Apify again.")
            with st.container(horizontal=True):
                if st.button("Continue last search", type="primary", icon=":material/play_arrow:",
                             disabled=not (apify_token and llm["api_key"])):
                    run_search(store, latest["_id"], apify_token, llm, background, template_body, note)
                    st.rerun()
                if st.button("Discard it", icon=":material/delete:"):
                    store.archive(latest["_id"])
                    st.rerun()

    keywords = st.text_area("Search keywords (one search per line)", key="li_keywords", height=90,
                            placeholder='"hiring" AND "GenAI engineer"\nAI ML engineer hiring email')
    with st.container(horizontal=True):
        st.number_input("Max posts per search", 1, 500, step=10, key="li_max")
        st.selectbox("Posted", list(auto.POSTED_OPTIONS), key="li_posted")
        st.selectbox("Sort by", list(auto.SORT_OPTIONS), key="li_sort")

    queries = [q.strip() for q in keywords.splitlines() if q.strip()]
    need = [n for n, v in (("Apify key", apify_token), (f"{llm['name']} key", llm["api_key"])) if not v]
    if st.button("Find contacts and write emails", icon=":material/travel_explore:", type="primary",
                 disabled=bool(need or not queries)):
        if latest:
            store.archive(latest["_id"])
        search_id = store.new({"queries": queries, "max_posts": st.session_state.li_max,
                               "sort": st.session_state.li_sort, "posted": st.session_state.li_posted})
        run_search(store, search_id, apify_token, llm, background, template_body, note)
        st.rerun()
    if need:
        st.caption("Missing: " + " and ".join(need) + ". Add your own in Profile setup.")
    elif not queries:
        st.caption("Add at least one search keyword.")

    if not latest or latest["status"] != "done":
        return None, None
    rows, fallback = search_rows(latest, template_body)
    if not rows and not fallback:
        return None, None
    with st.container(horizontal=True, vertical_alignment="center"):
        when = latest["created_at"].astimezone(user_timezone()).strftime("%d %b, %H:%M")
        st.caption(f"Contacts from your search on {when}: {len(rows) + len(fallback)} addresses.", width="stretch")
        st.download_button("CSV", auto.rows_to_csv(rows + fallback), file_name="linkedin_contacts.csv",
                           mime="text/csv", icon=":material/download:", help="Download the contacts and emails.")
        if st.button("Clear", icon=":material/close:", help="Hide these contacts. Start a new search above."):
            store.archive(latest["_id"])
            st.rerun()
    if fallback:
        with st.container(horizontal=True, vertical_alignment="center"):
            st.caption(f":orange[{len(fallback)} contacts use your fallback email (Advanced) because the AI "
                       f"could not write one.]", width="stretch")
            if st.button("Retry AI", icon=":material/refresh:", disabled=not llm["api_key"]):
                store.update(latest["_id"], status="writing")
                run_search(store, latest["_id"], apify_token, llm, background, template_body, note)
                st.rerun()
    return auto.rows_to_csv(rows + fallback), f"search_{latest['_id']}_{len(rows)}_{len(fallback)}"


def job_description(contact):
    jd = contact["extras"].get("Job Description", "")
    if jd:
        return jd
    if contact["Role / Job"]:
        return f"Role: {contact['Role / Job']}" + (f" at {contact['Company']}" if contact["Company"] else "")
    return ""


def tailoring_key(base, contact, note):
    return (base.file_id, job_description(contact), contact["Company"], note.strip())


def tailor_resumes(llm, base, base_text, contacts, note):
    """Generate a tailored PDF for every contact that has a job description and none cached yet."""
    cache = st.session_state.setdefault("tailored", {})
    todo = [c for c in contacts if job_description(c) and tailoring_key(base, c, note) not in cache]
    progress = st.progress(0.0, text="Tailoring resumes…")
    failed = []

    def one(contact):
        data = auto.tailor_resume(llm, base_text, job_description(contact), note)
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


class StoredFile:
    """A resume from the profile, with the same interface as an uploaded file."""

    def __init__(self, d):
        self.name, self.type, self._content = d["name"], d.get("type") or "application/pdf", d["content"]
        self.file_id = hashlib.sha1(self._content).hexdigest()

    def getvalue(self):
        return self._content


def init_form_defaults():
    """Default widget values. Values from the signed-in profile win; widgets keep what was typed.
    The app's own API keys are not put in any box: they're used only when a box is empty."""
    defaults = {
        "use_groq": True, "csv_mode": False,
        "subject": "Application for [Role]", "body_text": TEMPLATE_BODY.read_text(encoding="utf-8"),
        "fb_contact": DEFAULT_FALLBACKS["Contact Person"], "fb_company": DEFAULT_FALLBACKS["Company"],
        "fb_role": DEFAULT_FALLBACKS["Role / Job"], "delay": 3,
        "li_max": 50, "li_posted": list(auto.POSTED_OPTIONS)[2], "li_sort": list(auto.SORT_OPTIONS)[0],
        "li_keywords": "", "li_note": "",
    }
    defaults.update({k: v for k, v in profile_ui.data().items() if k in profile_ui.PROFILE_KEYS})
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


NOTE_HINT = ("Tip: add your portfolio, GitHub or LinkedIn links here, and anything the AI should always mention "
             "(e.g. notice period). The AI also picks the key highlights from your resume for the subject and body.")


def main():
    init_form_defaults()
    profile_ui.autosave()
    profile_ui.hide_enter_hint()
    with st.container(horizontal=True, vertical_alignment="center"):
        st.title(":material/forward_to_inbox: Bulk email sender", width="stretch")
        st.link_button("LinkedIn", LINKEDIN_URL, icon=":material/person:")
    if st.session_state.pop("_goto_setup", False):  # just registered: the profile page also saves the login
        st.switch_page("pages/profile.py")
    profile_ui.restore()
    profile_ui.gate()
    profile_ui.panel()
    username, uri, saved = profile_ui.user(), profile_ui.mongo_uri(), profile_ui.data()

    # Sections in the order they're shown; filled below in the order the logic needs them.
    details_box = st.container(border=True)
    jobs_box = st.container()
    find_box = st.container(border=True)
    review_box = st.container()
    tailor_box = st.container()
    send_box = st.container(border=True)
    advanced = st.expander("Advanced", icon=":material/tune:")

    # ---------- Advanced (rarely changed) ----------
    with advanced:
        csv_mode = st.toggle("Upload my own contacts CSV instead of searching LinkedIn", key="csv_mode")
        tailor = st.toggle("Tailor resume to each job", key="tailor_mode",
                           help="The AI rewrites your resume for each job description and attaches that PDF "
                                "instead of your base resume. Uses only facts already in your resume.")
        use_groq = st.toggle("Use Groq for the AI (off: Google Gemini)", key="use_groq")
        models = auto.GROQ_MODELS if use_groq else auto.GEMINI_MODELS
        st.selectbox("AI model", list(models), key="groq_model" if use_groq else "gemini_model",
                     accept_new_options=True, persist_state="session",
                     format_func=lambda m: f"{m}: {models[m]}" if m in models else m)
        st.text_area("Note for the AI", key="li_note", height=70, help=NOTE_HINT,
                     placeholder="e.g. Portfolio: https://... Mention I can join in 30 days.")
        st.caption(NOTE_HINT)
        extra_files = st.file_uploader("Extra attachments (sent with every email)", accept_multiple_files=True,
                                       key="extra_files")
        excluded_company = st.text_input(
            "Skip your current company", key="excluded_company", placeholder="e.g. Strelema",
            help="Contacts whose Company or email domain matches are not emailed. Separate several with commas.")
        st.divider()
        st.markdown("**Fallback email**")
        st.caption("Used for contacts from your CSV and when the AI can't write an email. Placeholders: "
                   + "  ".join(f"`{p}`" for p in PLACEHOLDERS) + "  `[LinkedIn Post]`")
        subject = st.text_input("Subject", key="subject")
        body_message = st.text_area("Body", height=300, key="body_text")
        st.markdown("**Used when a cell is blank**")
        fallbacks = {
            "Contact Person": st.text_input("Contact Person", key="fb_contact"),
            "Company": st.text_input("Company", key="fb_company"),
            "Role / Job": st.text_input("Role / Job", key="fb_role"),
        }
        fallbacks["First Name"] = fallbacks["Contact Person"]
        extra_fallback_box = st.container()
        st.divider()
        delay = st.slider("Seconds between emails", 0, 30, key="delay",
                          help="A small delay lowers the chance of Gmail flagging the batch. "
                               "Gmail allows roughly 500 emails/day from a personal account.")

    # ---------- 1. Who sends ----------
    with details_box:
        if username:
            sender = username
            password = (saved.get("password") or "").replace(" ", "")
            stored = profile_ui.resume()
            resume_file = StoredFile(stored) if stored else None
            own = {k: saved.get(k, "") for k in ("apify_token", "groq_api_key", "gemini_api_key")}
            with st.container(horizontal=True, vertical_alignment="center"):
                st.markdown(f":material/mail: Sending from **{sender}**  \n:material/description: Resume: "
                            + (f"**{resume_file.name}**" if resume_file else ":orange[none yet]"), width="stretch")
                st.page_link("pages/profile.py", label="Edit profile", icon=":material/edit:")
            if not password:
                st.warning("Add your Gmail app password in your profile to send emails.", icon=":material/key:")
        else:
            st.markdown("**1. Your details**")
            sender = st.text_input("Gmail address", key="sender", placeholder="you@gmail.com")
            password = profile_ui.key_input("App password", "gmail", key="password",
                                            placeholder="16 characters, spaces are fine").replace(" ", "")
            resume_file = st.file_uploader("Resume (PDF, DOCX or TXT)", type=["pdf", "docx", "txt"],
                                           key="guest_resume")
            with st.expander("Your own API keys (optional)", icon=":material/key:"):
                st.caption("Leave blank to use the app's shared keys.")
                own = {
                    "apify_token": profile_ui.key_input("Apify API token", "apify", key="apify_token"),
                    "groq_api_key": profile_ui.key_input("Groq API key", "groq", key="groq_api_key"),
                    "gemini_api_key": profile_ui.key_input("Gemini API key", "gemini", key="gemini_api_key"),
                }
        apify_token = own["apify_token"] or profile_ui.default_key("apify")
        llm = llm_settings(use_groq, (own["groq_api_key"] or profile_ui.default_key("groq")) if use_groq
                           else (own["gemini_api_key"] or profile_ui.default_key("gemini")))

    with jobs_box:
        scheduled_jobs(sender)

    # ---------- 2. Contacts ----------
    with find_box:
        if csv_mode:
            st.markdown("**2. Your contacts CSV**")
            with st.container(horizontal=True, vertical_alignment="bottom"):
                csv_file = st.file_uploader("Contacts CSV", type=["csv"])
                st.download_button("Template CSV", data=TEMPLATE_CSV.read_bytes(), file_name="contacts_template.csv",
                                   mime="text/csv", icon=":material/download:",
                                   help="Columns: Email (required), Contact Person, Company, Role / Job. Optional: "
                                        "Subject and Message to write a custom email for that row.")
            csv_bytes, csv_key = (csv_file.getvalue(), csv_file.file_id) if csv_file else (None, None)
        else:
            st.markdown("**2. Find contacts on LinkedIn**")
            background = background_text([resume_file] + list(extra_files or []) if resume_file else extra_files)
            csv_bytes, csv_key = linkedin_finder(SearchStore(uri, username), apify_token, llm, background,
                                                 body_message)

    contacts, extra_cols = [], []
    if csv_bytes:
        with review_box:
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
                last = {}
                if username:
                    try:
                        last = profiles.last_sent(uri, username, [c["Email"] for c in parsed])
                    except Exception as e:
                        st.warning(f"Could not check your send history, so earlier recipients are not unticked: {e}")
                tz = user_timezone()
                last_of = {c["Email"]: last.get(c["Email"].lower()) for c in parsed}
                fmt = lambda t: t.astimezone(tz).strftime("%d %b %Y") if t else ""
                recent = [c for c in parsed if profiles.emailed_recently(last_of[c["Email"]])]
                older = sum(t is not None for t in last_of.values()) - len(recent)
                resend = False
                if recent:
                    # Emailed within the last month: skipped unless the user asks to send again.
                    with st.container(border=True):
                        st.markdown(f":material/block: **{len(recent)} of these contacts already got an email from "
                                    f"you in the last {profiles.RESEND_AFTER_DAYS} days**, so they are skipped.")
                        st.dataframe(pd.DataFrame([{"Email": c["Email"], "Company": c["Company"],
                                                    "Last emailed": fmt(last_of[c["Email"]])} for c in recent]),
                                     hide_index=True, height=min(35 * len(recent) + 38, 220))
                        resend = st.checkbox("Email them again anyway", key=f"resend_{csv_key}")
                if older:
                    st.caption(f":material/history: {older} contacts were emailed more than "
                               f"{profiles.RESEND_AFTER_DAYS} days ago and are included again.")
                to_send = len(parsed) - (0 if resend else len(recent))
                with st.expander(f"Review recipients: {to_send} to send"
                                 + (f", {len(recent)} already emailed" if recent and not resend else "")
                                 + (f", {len(skipped)} skipped" if skipped else ""), icon=":material/group:"):
                    st.caption("Edit any cell or untick **Send** to exclude a row.")
                    shown = FIELDS + [f for f in CUSTOM_FIELDS if any(c[f] for c in parsed)]
                    recent_emails = {c["Email"] for c in recent}
                    table = pd.DataFrame([
                        {"Send": resend or c["Email"] not in recent_emails, **{f: c[f] for f in shown},
                         **c["extras"], "Blank → fallback": ", ".join(sorted(c["missing"])),
                         **({"Last emailed": fmt(last_of[c["Email"]])} if username else {})}
                        for c in parsed
                    ])
                    edited = st.data_editor(table, hide_index=True, disabled=["Blank → fallback", "Last emailed"],
                                            column_config={"Message": st.column_config.TextColumn(width="large"),
                                                           "Job Description": st.column_config.TextColumn(width="large"),
                                                           "LinkedIn Post": st.column_config.LinkColumn(display_text="Open post")},
                                            key=f"editor_{csv_key}_{excluded_company}_{resend}_"
                                                f"{st.session_state.get('history_rev', 0)}")
                    contacts = to_contacts(edited, extra_cols)
                    if skipped:
                        st.markdown("**Skipped** (invalid, duplicate, or your current company)")
                        st.dataframe(pd.DataFrame(skipped), hide_index=True)
    with extra_fallback_box:
        for col in extra_cols:
            fallbacks[col] = st.text_input(col, key=f"fb_extra_{col}", persist_state="session")

    # ---------- Tailored resumes (optional) ----------
    extra_data = read_attachments(extra_files)
    attachment_data = read_attachments([resume_file]) + extra_data if resume_file else extra_data
    if tailor:
        with tailor_box, st.container(border=True):
            st.markdown("**3. Tailored resumes**")
            tailor_note = st.text_input("Note for the AI (optional)", key="tailor_note",
                                        placeholder="e.g. Keep it to one page, put Projects before Experience")
            if not resume_file:
                st.info("Add your resume first" + (" in your profile." if username else " above."),
                        icon=":material/description:")
            elif contacts:
                try:
                    base_text = auto.resume_text(resume_file.name, resume_file.getvalue())
                except Exception as e:
                    st.error(f"Could not read the resume: {e}")
                    base_text = ""
                cache = st.session_state.get("tailored", {})
                with_jd = [c for c in contacts if job_description(c)]
                done = sum(tailoring_key(resume_file, c, tailor_note) in cache for c in with_jd)
                st.caption(f"{done} of {len(with_jd)} resumes tailored. Contacts without a job description or role "
                           f"({len(contacts) - len(with_jd)}) get your base resume. The tailored PDF uses a simple "
                           "layout, not your original design, so check one before sending.")
                if base_text and done < len(with_jd):
                    if st.button(f"Tailor {len(with_jd) - done} resumes", icon=":material/auto_fix_high:",
                                 disabled=not llm["api_key"]):
                        tailor_resumes(llm, resume_file, base_text, with_jd, tailor_note)
                        st.rerun()

            # Each contact gets the extra files plus either its tailored PDF or the base resume.
            if resume_file:
                base_file = read_attachments([resume_file])[0]
                cache = st.session_state.get("tailored", {})
                for c in contacts:
                    hit = cache.get(tailoring_key(resume_file, c, tailor_note))
                    resume_att = ({"filename": hit[0], "content": hit[1], "maintype": "application",
                                   "subtype": "pdf"} if hit else base_file)
                    c["attachments"] = extra_data + [resume_att]

    # ---------- Preview & send ----------
    with send_box:
        st.markdown(f"**{4 if tailor else 3}. Preview and send**")
        if not contacts:
            st.info("Upload a contacts CSV to see a preview." if csv_mode
                    else "Find contacts on LinkedIn above to see a preview.", icon=":material/preview:")
            return

        pick = st.selectbox("Preview for", range(len(contacts)), label_visibility="collapsed",
                            format_func=lambda i: f"Preview: {contacts[i]['Email']} ({contacts[i]['Contact Person'] or 'no name'})")
        preview_subject, preview_body = compose(contacts[pick], subject, body_message, fallbacks)
        own_text = [f.lower() for f in CUSTOM_FIELDS if contacts[pick][f]]
        st.markdown(f"**Subject:** {preview_subject}"
                    + ("  :blue-badge[Written for this contact]" if own_text else ""))
        with st.container(height=250):
            st.text(preview_body)
        files = contacts[pick].get("attachments", attachment_data)
        st.caption("Attachments: " + (", ".join(a["filename"] for a in files) if files else "none"))
        tailored = [a for a in files if resume_file and a["filename"] != resume_file.name
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
            st.caption("Add your " + " and ".join(missing) + (" in your profile" if username else " in step 1")
                       + " to send.")

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
            on_done = (lambda results: profiles.record_sends(uri, username, results)) if username else None
            get_scheduler().schedule(send_at.astimezone(timezone.utc), sender, password, subject, body_message,
                                     contacts, attachment_data, fallbacks, delay, on_done=on_done)
            st.success(f"Scheduled {len(contacts)} emails for {send_at:%a %d %b, %H:%M}. "
                       "You can close this tab; progress shows at the top.")
            return

        results = send_bulk_emails(sender, password, subject, body_message, contacts,
                                   attachment_data, fallbacks, delay)
        if results is None:
            return
        if username:
            try:
                profiles.record_sends(uri, username, results)
                st.session_state.history_rev = st.session_state.get("history_rev", 0) + 1
            except Exception as e:
                st.warning(f"Emails sent, but the send history was not saved: {e}")
        sent = sum(r["Status"] == "Sent" for r in results)
        failed = len(results) - sent
        (st.success if not failed else st.warning)(f"Done: {sent} sent, {failed} failed.")
        results_df = pd.DataFrame(results).drop(columns=["Message-ID"])
        st.dataframe(results_df, hide_index=True)
        st.download_button("Download send report", results_df.to_csv(index=False).encode(),
                           file_name="send_report.csv", mime="text/csv", icon=":material/download:")


if __name__ == "__main__":
    main()
