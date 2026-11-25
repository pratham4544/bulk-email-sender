"""Streamlit side of profiles: start screen (log in / register / guest), "keep me logged in", saving.

Session state used:
  profile_user    the signed-in Gmail address
  profile_key     encryption key (derived from the login password, kept only in server memory)
  profile_data    the decrypted profile, also used to refill widgets when coming back from another page
  profile_resume  {"name", "type", "content"} of the saved resume, or None
  session_token   the "keep me logged in" token of this browser, if any
  guest_mode      True once someone chose "Continue as guest"
"""
import os
from pathlib import Path

import streamlit as st

import profiles

BASE_DIR = Path(__file__).parent
TOKEN_STORAGE_KEY = "bulk_email_sender_login"

# Saved per profile (widget keys of the main, profile and follow-up pages).
SECRET_KEYS = ["password", "apify_token", "groq_api_key", "gemini_api_key"]
PROFILE_KEYS = SECRET_KEYS + [
    "excluded_company", "subject", "body_text", "csv_mode", "tailor_mode", "use_groq",
    "fb_contact", "fb_company", "fb_role", "delay", "groq_model", "gemini_model",
    "tailor_note", "li_keywords", "li_max", "li_sort", "li_posted", "li_note",
    "followup_days", "followup_subject", "followup_body",
]


def env_value(name):
    """A value from the environment or the project's .env file (KEY=value lines)."""
    if os.environ.get(name):
        return os.environ[name]
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip().removeprefix("export ").strip() == name:
                return value.strip().strip("'\"")
    return ""


def secret(name):
    try:
        return st.secrets.get(name, "")
    except Exception:
        return ""


def mongo_uri():
    return secret("MONGODB_URI") or env_value("MONGODB_URI")


# The app's own keys (Streamlit secrets or .env). Used when a user has not entered their own; never shown.
_DEFAULT_KEY_NAMES = {
    "apify": ("APIFY_TOKEN", "apify_token", "APIFY_API_TOKEN"),
    "groq": ("GROQ_API_KEY", "groq_api_key"),
    "gemini": ("GEMINI_API_KEY", "gemini_api_key"),
}


def default_key(service):
    for name in _DEFAULT_KEY_NAMES[service]:
        value = secret(name) or env_value(name)
        if value:
            return value
    return ""


def user():
    return st.session_state.get("profile_user")


def data():
    return st.session_state.get("profile_data", {})


def resume():
    return st.session_state.get("profile_resume")


# Where each key is created: the button next to each field opens this page directly.
KEY_PAGES = {
    "gmail": ("Create app password", "https://myaccount.google.com/apppasswords",
              "Not your normal Gmail password. 2-Step Verification must be on. On the page that opens, type a "
              "name (e.g. Bulk email sender), press Create and paste the 16 characters here."),
    "apify": ("Get Apify token", "https://console.apify.com/settings/integrations",
              "Sign up free at apify.com, then copy the Personal API token (starts with apify_api_). "
              "Apify charges per LinkedIn post scraped."),
    "groq": ("Get Groq key", "https://console.groq.com/keys",
             "Sign in, press Create API Key and copy it (starts with gsk_). It is shown only once."),
    "gemini": ("Get Gemini key", "https://aistudio.google.com/apikey",
               "Sign in with Google, press Create API key and copy it."),
}


def key_input(label, service, **kwargs):
    """A password-style text box with a button that opens the page where that key is created."""
    button, url, help_text = KEY_PAGES[service]
    with st.container(horizontal=True, vertical_alignment="bottom"):
        value = st.text_input(label, type="password", help=help_text, **kwargs)
        st.link_button(button, url, icon=":material/open_in_new:")
    return value


def hide_enter_hint():
    """Values are applied when the box loses focus too, so the "Press Enter to apply" hint only confuses."""
    st.html("<style>[data-testid='InputInstructions']{display:none}</style>")


# ---------- browser memory for "keep me logged in" ----------

_JS = """
export default function (component) {
  const { data, setStateValue } = component
  const k = data.storage_key
  try {
    if (data.clear) window.localStorage.removeItem(k)
    if (data.set) window.localStorage.setItem(k, data.set)
  } catch (e) {}
  let token = ""
  try { token = window.localStorage.getItem(k) || "" } catch (e) {}
  setStateValue("token", token)
}
"""
def _browser_memory(set_token=None, clear=False):
    # Registered on use (not at import) so it is known to whichever Streamlit runtime is running.
    memory = st.components.v2.component("login_memory", html="<span></span>", js=_JS)
    memory(key="_login_memory", data={"storage_key": TOKEN_STORAGE_KEY, "set": set_token, "clear": clear},
            height=0, on_token_change=lambda: None)


def restore():
    """Log in from this browser's saved token, and write/clear the token after logging in/out.
    Call near the top of every page."""
    s = st.session_state
    if not mongo_uri():
        return
    set_token, clear = s.pop("_set_token", None), s.pop("_forget", False)
    if user():
        if set_token:
            _browser_memory(set_token=set_token)
        return
    state = s.get("_login_memory")
    token = state.get("token") if hasattr(state, "get") else None
    if token and not clear and s.get("_tried_token") != token:
        s._tried_token = token
        try:
            found = profiles.resume_session(mongo_uri(), token)
        except Exception:
            found = None
        if found:
            _sign_in(*found)
            s.session_token = token
            st.rerun()
    _browser_memory(clear=clear)


# ---------- log in / register / log out ----------

def _sign_in(username, key, remember=False):
    s = st.session_state
    uri = mongo_uri()
    loaded = profiles.load(uri, username, key)
    s.profile_user = profiles.normalize(username)
    s.profile_key = key
    s.profile_data = loaded
    s.profile_resume = profiles.load_resume(uri, username, key)
    s.guest_mode = False
    for k, v in loaded.items():
        if k in PROFILE_KEYS:
            s[k] = v
    if remember:
        s.session_token = s._tried_token = s._set_token = profiles.create_session(uri, username, key)


def _on_login():
    s = st.session_state
    try:
        key = profiles.authenticate(mongo_uri(), s.get("login_user", ""), s.get("login_pw", ""))
    except Exception as e:
        s.login_error = f"Could not reach the database: {e}"
        return
    if key is None:
        s.login_error = "Wrong Gmail or password."
        return
    _sign_in(s.login_user, key, remember=s.get("login_remember", True))
    s.login_pw = s.login_error = ""


def _on_register():
    s = st.session_state
    if s.get("reg_pw") != s.get("reg_pw2"):
        s.reg_error = "The passwords don't match."
        return
    try:
        uri = mongo_uri()
        key = profiles.create_user(uri, s.get("reg_user", ""), s.get("reg_pw", ""))
        file = s.get("reg_resume")
        if file:
            profiles.save_resume(uri, s.reg_user, key, file.name, file.type or "application/pdf", file.getvalue())
    except ValueError as e:
        s.reg_error = str(e)
        return
    except Exception as e:
        s.reg_error = f"Could not reach the database: {e}"
        return
    _sign_in(s.reg_user, key, remember=s.get("reg_remember", True))
    autosave()  # what a guest already typed becomes part of the new profile
    s.reg_pw = s.reg_pw2 = s.reg_error = ""
    s._goto_setup = True


def _on_logout():
    s = st.session_state
    if s.get("session_token"):
        try:
            profiles.end_session(mongo_uri(), s.session_token)
        except Exception:
            pass
    for k in ["profile_user", "profile_key", "profile_data", "profile_resume", "session_token", "guest_mode",
              "guest_search", "tailored", *PROFILE_KEYS]:
        s.pop(k, None)
    s._forget = True


def _set_guest(on):
    st.session_state.guest_mode = on


def autosave():
    """Save profile values that changed since the last save. Call near the top of a page, after defaults."""
    if not user():
        return
    current = data()
    merged = {**current, **{k: st.session_state[k] for k in PROFILE_KEYS if k in st.session_state}}
    if merged != current:
        try:
            profiles.save(mongo_uri(), user(), st.session_state.profile_key, merged)
            st.session_state.profile_data = merged
        except Exception as e:
            st.toast(f"Profile not saved: {e}", icon=":material/error:")


def update_profile(values):
    """Save the given values now (the Profile setup page) and use them everywhere in this session."""
    merged = {**data(), **values}
    profiles.save(mongo_uri(), user(), st.session_state.profile_key, merged)
    st.session_state.profile_data = merged
    for k, v in values.items():
        st.session_state[k] = v


def set_resume(file):
    profiles.save_resume(mongo_uri(), user(), st.session_state.profile_key, file.name,
                         file.type or "application/pdf", file.getvalue())
    st.session_state.profile_resume = {"name": file.name, "type": file.type, "content": file.getvalue()}


# ---------- screens ----------

def gate():
    """Start screen: log in, register or continue as guest. Stops the page until one is chosen.

    Without MONGODB_URI there are no profiles, so everyone is a guest and the screen is skipped.
    """
    if not mongo_uri() or user() or st.session_state.get("guest_mode"):
        return
    # The form isn't drawn here, so Streamlit would drop its values; re-setting them keeps what a guest typed.
    for k in PROFILE_KEYS:
        if k in st.session_state:
            st.session_state[k] = st.session_state[k]

    with st.container(border=True):
        st.subheader("Log in, register or use as guest")
        login, register = st.tabs(["Log in", "Register"])
        with login:
            with st.form("login_form", border=False):
                st.text_input("Gmail", key="login_user", placeholder="you@gmail.com")
                st.text_input("Password", type="password", key="login_pw")
                st.checkbox("Keep me logged in on this device for 30 days", value=True, key="login_remember")
                st.form_submit_button("Log in", type="primary", on_click=_on_login, width="stretch")
            if st.session_state.get("login_error"):
                st.error(st.session_state.login_error)
        with register:
            with st.form("register_form", border=False):
                st.text_input("Gmail", key="reg_user", placeholder="you@gmail.com",
                              help="The Gmail you send from. You add its app password in the next step.")
                st.text_input("Password for this app", type="password", key="reg_pw",
                              help=f"At least {profiles.MIN_PASSWORD} characters. Not your Gmail password.")
                st.text_input("Repeat password", type="password", key="reg_pw2")
                st.file_uploader("Resume (PDF, DOCX or TXT)", type=["pdf", "docx", "txt"], key="reg_resume",
                                 help="Attached to your emails and used by the AI to write them. "
                                      "You can add or change it later.")
                st.warning("There is no password reset. If you forget it, you have to register again, "
                           "so write it down somewhere safe.", icon=":material/warning:")
                st.checkbox("Keep me logged in on this device for 30 days", value=True, key="reg_remember")
                st.form_submit_button("Register", type="primary", on_click=_on_register, width="stretch")
            if st.session_state.get("reg_error"):
                st.error(st.session_state.reg_error)
    with st.container(horizontal=True, vertical_alignment="center"):
        st.caption("Just want to send once? Guests can use everything, but nothing is saved: no history, "
                   "no skipping people you already emailed, no follow-ups.", width="stretch")
        st.button("Continue as guest", icon=":material/person_outline:", on_click=_set_guest, args=(True,))
    st.stop()


def panel():
    """Profile bar on the main page: who is signed in, or a guest notice with a way to log in."""
    if not mongo_uri():
        st.caption(":material/info: Profiles are off: add `MONGODB_URI` to the app's secrets to save your keys, "
                   "template and send history.")
        return
    if user():
        with st.container(horizontal=True, vertical_alignment="center"):
            st.markdown(f":material/account_circle: **{user()}**", width="stretch")
            st.page_link("pages/profile.py", label="Profile", icon=":material/manage_accounts:")
            st.page_link("pages/history.py", label="History", icon=":material/history:")
            st.page_link("pages/followups.py", label="Follow-ups", icon=":material/reply:")
            st.button("Log out", on_click=_on_logout, icon=":material/logout:")
        return
    with st.container(horizontal=True, vertical_alignment="center"):
        st.markdown(":material/person_outline: **Guest**: nothing is saved, and people you emailed before are "
                    "not skipped.", width="stretch")
        st.button("Log in or register", on_click=_set_guest, args=(False,), icon=":material/login:",
                  help="What you typed so far is kept. Register to save it.")


def require_login():
    """For the profile, history and follow-up pages: stop with a hint when nobody is signed in."""
    if not mongo_uri():
        st.info("Profiles are off: add `MONGODB_URI` to the app's secrets.", icon=":material/info:")
        st.stop()
    restore()
    if not user():
        st.info("This page is for registered users. Guests' details and emails are not saved.",
                icon=":material/account_circle:")
        if st.button("Log in or register", type="primary", icon=":material/login:"):
            _set_guest(False)
            st.switch_page("app.py")
        st.stop()
    return user()
