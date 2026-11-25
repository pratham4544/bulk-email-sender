"""User profiles and send history in MongoDB. No Streamlit calls, so the scheduler thread can record sends.

- users: one document per profile. The login password is stored only as a scrypt hash. Everything the
  profile saves (Gmail address, app password, API keys, subject, body, settings) is one encrypted blob,
  keyed by the login password, so the database alone can't reveal it. Forgetting the password means
  starting a new profile.
- sends: one document per email sent, used for the history page, follow-ups and to avoid emailing the
  same address twice.
- sessions: "keep me logged in" tokens. The browser keeps the token; the database keeps only its hash and the
  profile key encrypted with it, so neither side alone can open a profile. Expire after SESSION_DAYS.
- searches: each LinkedIn search, saved step by step (Apify run id, posts, written emails), so an error or a
  closed tab never loses results that were already paid for.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError
from bson import ObjectId

DB_NAME = "bulk_email_sender"
EMAIL_RE = re.compile(r"^[a-z0-9._%+'-]+@[a-z0-9.-]+\.[a-z]{2,}$")
MIN_PASSWORD = 8
SESSION_DAYS = 30
MAX_RESUME_BYTES = 5 * 1024 * 1024
RESEND_AFTER_DAYS = 30  # emailed within this many days: skipped unless the user ticks "email again"

_clients = {}
_lock = threading.Lock()


def _db(uri):
    """One client per URI for the whole process (pymongo clients are thread-safe and pooled)."""
    with _lock:
        if uri not in _clients:
            client = MongoClient(uri, serverSelectionTimeoutMS=8000, tz_aware=True)
            db = client.get_default_database(DB_NAME)
            db.sends.create_index([("user", ASCENDING), ("email", ASCENDING)])
            db.sends.create_index([("user", ASCENDING), ("sent_at", DESCENDING)])
            db.searches.create_index([("user", ASCENDING), ("created_at", DESCENDING)])
            db.sessions.create_index("expires", expireAfterSeconds=0)
            _clients[uri] = db
        return _clients[uri]


def _scrypt(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)


def _fernet(key):
    return Fernet(base64.urlsafe_b64encode(key))


def normalize(username):
    return (username or "").strip().lower()


def create_user(uri, username, password):
    """Create a profile (the username is the user's Gmail address) and return its encryption key.
    Raises ValueError with a message for the user."""
    username = normalize(username)
    if not EMAIL_RE.match(username):
        raise ValueError("Enter a valid email address, e.g. you@gmail.com.")
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Password must be at least {MIN_PASSWORD} characters.")
    pw_salt, enc_salt = os.urandom(16), os.urandom(16)
    key = _scrypt(password, enc_salt)
    try:
        _db(uri).users.insert_one({
            "_id": username, "pw_salt": pw_salt, "pw_hash": _scrypt(password, pw_salt), "enc_salt": enc_salt,
            "data": _fernet(key).encrypt(b"{}"), "created_at": datetime.now(timezone.utc),
        })
    except DuplicateKeyError:
        raise ValueError("A profile with this email already exists. Log in instead.") from None
    return key


def authenticate(uri, username, password):
    """The profile's encryption key if the password is right, else None."""
    user = _db(uri).users.find_one({"_id": normalize(username)})
    if not user or not hmac.compare_digest(_scrypt(password, user["pw_salt"]), user["pw_hash"]):
        return None
    return _scrypt(password, user["enc_salt"])


def load(uri, username, key):
    user = _db(uri).users.find_one({"_id": normalize(username)}, {"data": 1})
    try:
        return json.loads(_fernet(key).decrypt(user["data"])) if user else {}
    except InvalidToken:
        return {}


def save(uri, username, key, data):
    _db(uri).users.update_one({"_id": normalize(username)},
                              {"$set": {"data": _fernet(key).encrypt(json.dumps(data).encode()),
                                        "updated_at": datetime.now(timezone.utc)}})


def save_resume(uri, username, key, name, mime, content):
    if len(content) > MAX_RESUME_BYTES:
        raise ValueError("The resume is larger than 5 MB.")
    blob = _fernet(key).encrypt(json.dumps({"name": name, "type": mime}).encode() + b"\n" + content)
    _db(uri).users.update_one({"_id": normalize(username)}, {"$set": {"resume": blob}})


def load_resume(uri, username, key):
    """{"name", "type", "content"} or None."""
    user = _db(uri).users.find_one({"_id": normalize(username)}, {"resume": 1})
    if not user or not user.get("resume"):
        return None
    try:
        meta, _, content = _fernet(key).decrypt(user["resume"]).partition(b"\n")
    except InvalidToken:
        return None
    return {**json.loads(meta), "content": content}


# ---------- "keep me logged in" ----------

def _token_parts(token):
    digest = hashlib.sha256(token.encode()).digest()
    return hashlib.sha256(b"id:" + digest).hexdigest(), hashlib.sha256(b"key:" + digest).digest()


def create_session(uri, username, key):
    """A new random token for the browser to keep."""
    token = secrets.token_urlsafe(32)
    sid, tkey = _token_parts(token)
    _db(uri).sessions.insert_one({"_id": sid, "user": normalize(username), "wrapped": _fernet(tkey).encrypt(key),
                                  "expires": datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)})
    return token


def resume_session(uri, token):
    """(username, key) for a valid, unexpired token, else None."""
    sid, tkey = _token_parts(token)
    doc = _db(uri).sessions.find_one({"_id": sid, "expires": {"$gt": datetime.now(timezone.utc)}})
    if not doc:
        return None
    try:
        return doc["user"], _fernet(tkey).decrypt(doc["wrapped"])
    except InvalidToken:
        return None


def end_session(uri, token):
    _db(uri).sessions.delete_one({"_id": _token_parts(token)[0]})


# ---------- LinkedIn searches ----------

def new_search(uri, username, params):
    now = datetime.now(timezone.utc)
    return _db(uri).searches.insert_one({
        "user": normalize(username), "params": params, "status": "scraping", "run_id": None, "posts": [],
        "total_posts": 0, "rows": [], "error": "", "archived": False, "created_at": now, "updated_at": now,
    }).inserted_id


def update_search(uri, search_id, **fields):
    _db(uri).searches.update_one({"_id": ObjectId(search_id)},
                                 {"$set": {**fields, "updated_at": datetime.now(timezone.utc)}})


def add_search_rows(uri, search_id, rows):
    _db(uri).searches.update_one({"_id": ObjectId(search_id)},
                                 {"$push": {"rows": {"$each": rows}}, "$set": {"updated_at": datetime.now(timezone.utc)}})


def latest_search(uri, username):
    return _db(uri).searches.find_one({"user": normalize(username), "archived": False},
                                      sort=[("created_at", DESCENDING)])


def get_search(uri, search_id):
    return _db(uri).searches.find_one({"_id": ObjectId(search_id)})


# ---------- send history ----------

def record_sends(uri, username, results, kind="email"):
    """Store the rows `deliver` returned. Failed rows are kept too (for the history) but never block a resend."""
    now = datetime.now(timezone.utc)
    docs = [{
        "user": normalize(username), "email": r["Email"].strip().lower(), "contact": r.get("Contact Person", ""),
        "company": r.get("Company", ""), "role": r.get("Role / Job", ""), "subject": r.get("Subject", ""),
        "status": r["Status"], "error": r.get("Error", ""), "message_id": r.get("Message-ID", ""),
        "kind": kind, "sent_at": now, "followups": 0, "last_followup_at": None, "replied": False,
    } for r in results]
    if docs:
        _db(uri).sends.insert_many(docs)


def last_sent(uri, username, emails):
    """{email: last successful send time} for the given addresses (lower-cased)."""
    emails = list({e.strip().lower() for e in emails})
    if not emails:
        return {}
    rows = _db(uri).sends.aggregate([
        {"$match": {"user": normalize(username), "email": {"$in": emails}, "status": "Sent"}},
        {"$group": {"_id": "$email", "at": {"$max": {"$ifNull": ["$last_followup_at", "$sent_at"]}}}},
    ])
    return {r["_id"]: r["at"] for r in rows}


def emailed_recently(last, now=None):
    """True if `last` (the last send to an address) is within RESEND_AFTER_DAYS."""
    return last is not None and (now or datetime.now(timezone.utc)) - last < timedelta(days=RESEND_AFTER_DAYS)


def history(uri, username, limit=5000):
    return list(_db(uri).sends.find({"user": normalize(username), "kind": "email"},
                                    sort=[("sent_at", DESCENDING)], limit=limit))


def followups_due(uri, username, days):
    """Sent emails, not marked replied, whose last touch (send or follow-up) is older than `days`."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return list(_db(uri).sends.find({
        "user": normalize(username), "kind": "email", "status": "Sent", "replied": False,
        "$or": [{"last_followup_at": None, "sent_at": {"$lt": cutoff}}, {"last_followup_at": {"$lt": cutoff}}],
    }, sort=[("sent_at", ASCENDING)]))


def mark_followed_up(uri, username, ids):
    _db(uri).sends.update_many({"user": normalize(username), "_id": {"$in": list(ids)}},
                               {"$inc": {"followups": 1}, "$set": {"last_followup_at": datetime.now(timezone.utc)}})


def mark_replied(uri, username, ids, replied=True):
    _db(uri).sends.update_many({"user": normalize(username), "_id": {"$in": list(ids)}}, {"$set": {"replied": replied}})
