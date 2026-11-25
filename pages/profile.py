"""Profile setup: everything needed to send, saved once and used on every visit."""
from pathlib import Path

import streamlit as st

import profile_ui

DEFAULT_SUBJECT = "Application for [Role]"
DEFAULT_BODY = (Path(__file__).parent.parent / "templates" / "email_template.txt").read_text(encoding="utf-8")

st.set_page_config(page_title="Profile setup", page_icon=":material/manage_accounts:")
st.title(":material/manage_accounts: Set up your profile")
username = profile_ui.require_login()
saved = profile_ui.data()
NOTE_HINT = ("Add your portfolio, GitHub or LinkedIn links and anything the AI should always mention (e.g. notice "
             "period). The AI also takes the key highlights from your resume for the subject and body.")

if not saved.get("password"):
    st.info("Fill this in once. Next time you log in, everything is already here.", icon=":material/waving_hand:")

with st.form("profile_form"):
    st.text_input("Gmail", value=username, disabled=True, help="Your login. Emails are sent from this address.")
    password = profile_ui.key_input("Gmail app password", "gmail", value=saved.get("password", ""),
                                    placeholder="16 characters, spaces are fine")

    resume = profile_ui.resume()
    st.markdown("**Resume** " + (f": {resume['name']}" if resume else ": :orange[none yet]"))
    new_resume = st.file_uploader("Upload a new resume" if resume else "Upload your resume",
                                  type=["pdf", "docx", "txt"], label_visibility="collapsed")

    keywords = st.text_area("Keywords for the LinkedIn search (one search per line)",
                            value=saved.get("li_keywords", ""), height=90,
                            placeholder='"hiring" AND "GenAI engineer"\nAI ML engineer hiring email')
    note = st.text_area("Note for the AI", value=saved.get("li_note", ""), height=90, help=NOTE_HINT,
                        placeholder="Portfolio: https://...  GitHub: https://...  I can join in 30 days.")
    st.caption(f":material/lightbulb: {NOTE_HINT}")
    excluded = st.text_input("Your current company (never emailed)", value=saved.get("excluded_company", ""),
                             placeholder="e.g. Strelema")

    st.markdown("**Fallback email**")
    st.caption("Sent when the AI can't write an email for a contact, and to contacts from your own CSV. "
               "Placeholders: `[First Name]` `[Recruiter Name]` `[Company]` `[Role]` `[LinkedIn Post]`")
    subject = st.text_input("Subject", value=saved.get("subject") or DEFAULT_SUBJECT)
    body = st.text_area("Body", value=saved.get("body_text") or DEFAULT_BODY, height=250)

    with st.expander("Your own API keys (optional)", icon=":material/key:"):
        st.caption("Leave blank to use the app's shared keys. Add your own if the shared ones run out.")
        apify = profile_ui.key_input("Apify API token", "apify", value=saved.get("apify_token", ""))
        groq = profile_ui.key_input("Groq API key", "groq", value=saved.get("groq_api_key", ""))
        gemini = profile_ui.key_input("Gemini API key", "gemini", value=saved.get("gemini_api_key", ""))

    submitted = st.form_submit_button("Save profile", type="primary", icon=":material/save:", width="stretch")

if submitted:
    try:
        profile_ui.update_profile({
            "password": password.strip(), "li_keywords": keywords, "li_note": note, "excluded_company": excluded,
            "subject": subject, "body_text": body,
            "apify_token": apify.strip(), "groq_api_key": groq.strip(), "gemini_api_key": gemini.strip(),
        })
        if new_resume:
            profile_ui.set_resume(new_resume)
    except ValueError as e:
        st.error(str(e))
    except Exception as e:
        st.error(f"Could not save: {e}")
    else:
        st.success("Saved.", icon=":material/check_circle:")
        st.page_link("app.py", label="Go find contacts and send", icon=":material/arrow_forward:")
