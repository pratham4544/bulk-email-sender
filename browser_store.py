"""Remember form values in the browser's localStorage so the next visit is pre-filled.

How it works: an invisible component reads localStorage once and sends the saved values to Python.
Python copies them into st.session_state (before any widget renders), and from then on every run
sends the current values back down so the component can write them to localStorage.
"""
import streamlit as st

STORAGE_KEY = "bulk_email_sender_form"
_KEY = "_browser_store"
_RESTORED = "_browser_store_restored"
_SAVED = "_browser_store_saved"

_JS = """
export default function (component) {
  const { data, setStateValue } = component
  const storageKey = data.storage_key
  if (data.save === null || data.save === undefined) {
    let saved = {}
    try { saved = JSON.parse(window.localStorage.getItem(storageKey) || "{}") || {} } catch (e) { saved = {} }
    setStateValue("loaded", saved)
  } else {
    try { window.localStorage.setItem(storageKey, JSON.stringify(data.save)) } catch (e) {}
  }
}
"""

_COMPONENT = st.components.v2.component("browser_store", html="<span></span>", js=_JS)


def sync(keys, secret_keys, remember_secrets_key, prefixes=()):
    """Restore saved values on the first run that has them, then save the current ones every run.

    Call at the top of the script, before the widgets in `keys` are created.
    Keys starting with one of `prefixes` are remembered too (for widgets created per CSV column).
    Values under `secret_keys` are only stored while st.session_state[remember_secrets_key] is truthy.
    """
    prefixes = tuple(prefixes)

    def remembered(k):
        return k in keys or (bool(prefixes) and k.startswith(prefixes))

    state = st.session_state.get(_KEY) or {}
    loaded = state.get("loaded") if hasattr(state, "get") else None

    if not st.session_state.get(_RESTORED) and loaded is not None:
        for k, v in loaded.items():
            if remembered(k) and v is not None:
                st.session_state[k] = v
        st.session_state[_SAVED] = {k: v for k, v in loaded.items() if remembered(k)}
        st.session_state[_RESTORED] = True

    save = None
    if st.session_state.get(_RESTORED):
        # Start from what was saved so values of widgets hidden this run are not lost.
        save = dict(st.session_state.get(_SAVED, {}))
        save.update({k: v for k, v in st.session_state.items() if isinstance(k, str) and remembered(k)})
        if not save.get(remember_secrets_key, st.session_state.get(remember_secrets_key)):
            for k in secret_keys:
                save.pop(k, None)
        st.session_state[_SAVED] = save

    _COMPONENT(key=_KEY, data={"storage_key": STORAGE_KEY, "save": save}, height=0,
               on_loaded_change=lambda: None)
