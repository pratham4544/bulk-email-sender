"""CSV parsing, cleaning and template rendering for Bulk Email Sender.

Kept free of Streamlit so it can be tested on its own.
"""
import csv
import io
import re
import unicodedata
from collections import defaultdict

# Canonical fields and the CSV headers accepted for each (case-insensitive).
FIELD_ALIASES = {
    "Email": ["email", "e-mail", "email address", "mail", "email id"],
    "Contact Person": ["contact person", "recruiter name", "recruiter", "name",
                       "contact name", "hr name", "full name"],
    "Company": ["company", "company name", "organization", "organisation", "employer"],
    "Role / Job": ["role / job", "role/job", "role", "job", "job title", "position",
                   "designation", "title"],
    # Optional per-row overrides of the default subject/body.
    "Subject": ["subject", "email subject", "subject line", "custom subject"],
    "Message": ["message", "body", "email body", "custom message", "custom email", "short email"],
}

# Placeholders usable in subject/body -> field they read from.
PLACEHOLDERS = {
    "[Recruiter Name]": "Contact Person",
    "[First Name]": "First Name",
    "[Company]": "Company",
    "[Role]": "Role / Job",
    "[Email]": "Email",
}

DEFAULT_FALLBACKS = {
    "Contact Person": "Hiring Manager",
    "First Name": "Hiring Manager",
    "Company": "your organization",
    "Role / Job": "AI/ML Engineer roles",
}

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

# Names that are mailbox labels rather than a person.
GENERIC_NAMES = {"info", "sales", "support", "hr", "admin", "careers", "career",
                 "jobs", "recruitment", "talent", "contact", "team", "hiring",
                 "indiajobs", "business", "profiles", "connect"}

def _strip_symbols(text):
    """NFKC-normalise (turns 𝗦𝗲𝗻𝗶𝗼𝗿 into Senior), fix 'amp;' and drop emoji."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("&amp;", "&").replace("amp;", "&")
    text = "".join(ch for ch in text
                   if unicodedata.category(ch) not in ("So", "Cs", "Co", "Mn", "Cf"))
    return re.sub(r"\s+", " ", text).strip()


def clean_name(name, email):
    name = _strip_symbols(name).strip(" .,-–|")
    if not name or not re.search(r"[A-Za-z]", name):
        return ""
    lowered = name.lower()
    local_part = email.split("@")[0].lower()
    # "Info", "Sales", or a name that is just the mailbox (e.g. "Enqorasolutionshr").
    if lowered in GENERIC_NAMES or lowered.replace(" ", "") == local_part:
        return ""
    if name.isupper() or name.islower():
        name = name.title()
    name = re.sub(r"\s*\.$", "", name)
    return name


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", _strip_symbols(text).lower())


def _soften_caps(text):
    """'AI ENGINEER – LEVEL II' -> 'AI Engineer – Level II' (short words kept as acronyms)."""
    if not text.isupper():
        return text
    return " ".join(w.capitalize() if len(w) > 3 else w for w in text.split())


def clean_company(company, contact_person):
    company = _strip_symbols(company).strip(" .,-–|")
    # Scraped data often copies the person's name into the company column.
    if not company or _norm(company) == _norm(contact_person):
        return ""
    return company


def clean_role(role):
    role = _strip_symbols(role)
    # A cell made only of hashtags ("#jobs #hiring") carries no role.
    if role and all(w.startswith("#") for w in role.split()):
        return ""
    role = role.replace("#", "").replace("_", " ")
    role = re.sub(r"^(for|at|in|a|an|now|alert)\b[\s:|–-]*", "", role, flags=re.I)
    role = _soften_caps(role.strip(" .,:;!&-–|*"))
    if len(role) < 2 or len(role) > 80 or not re.search(r"[A-Za-z]{2}", role):
        return ""
    if role.lower() in {"needs", "multiple tech roles", "multiple it opportunities"}:
        return ""
    return role


def _match_columns(header):
    """Map canonical field -> column index, plus extra columns by their own name."""
    mapping, extras = {}, {}
    for idx, col in enumerate(header):
        key = col.strip().lower()
        for field, aliases in FIELD_ALIASES.items():
            if key in aliases and field not in mapping:
                mapping[field] = idx
                break
        else:
            if col.strip():
                extras[col.strip()] = idx
    return mapping, extras


def parse_contacts(raw_bytes, shared_name_domains=3):
    """Parse an uploaded CSV into cleaned contact dicts.

    Returns (contacts, skipped, extra_columns). Each contact has the raw fields plus
    `missing`: the set of fields that were blank/unusable and will use a fallback.
    """
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        return [], [], []

    mapping, extras = _match_columns(rows[0])
    if "Email" in mapping:
        body = rows[1:]
    elif EMAIL_RE.match(rows[0][0].strip()):
        # Old format: one email per line, no header.
        mapping, extras, body = {"Email": 0}, {}, rows
    else:
        raise ValueError("Could not find an 'Email' column. Download the template CSV "
                         "to see the expected format.")

    def cell(row, idx):
        return row[idx].strip() if idx is not None and idx < len(row) else ""

    contacts, skipped, seen = [], [], set()
    for line_no, row in enumerate(body, start=2 if body is not rows else 1):
        email = cell(row, mapping["Email"])
        if not EMAIL_RE.match(email):
            skipped.append({"Line": line_no, "Email": email, "Reason": "Invalid email"})
            continue
        if email.lower() in seen:
            skipped.append({"Line": line_no, "Email": email, "Reason": "Duplicate"})
            continue
        seen.add(email.lower())

        raw_name = cell(row, mapping.get("Contact Person"))
        name = clean_name(raw_name, email)
        contact = {
            "Email": email,
            "Contact Person": name,
            "Company": clean_company(cell(row, mapping.get("Company")), raw_name),
            "Role / Job": clean_role(cell(row, mapping.get("Role / Job"))),
            "Subject": cell(row, mapping.get("Subject")),
            # Literal "\n" lets people type line breaks in tools that make real newlines awkward.
            "Message": cell(row, mapping.get("Message")).replace("\\n", "\n"),
            "extras": {col: cell(row, idx) for col, idx in extras.items()},
        }
        contacts.append(contact)

    # A name attached to many unrelated domains is a scraping artefact, not the recipient.
    domains_by_name = defaultdict(set)
    for c in contacts:
        if c["Contact Person"]:
            domains_by_name[c["Contact Person"].lower()].add(c["Email"].split("@")[1].lower())
    for c in contacts:
        if c["Contact Person"] and len(domains_by_name[c["Contact Person"].lower()]) >= shared_name_domains:
            c["Contact Person"] = ""

    for c in contacts:
        c["First Name"] = c["Contact Person"].split()[0] if c["Contact Person"] else ""
        c["missing"] = {f for f in ("Contact Person", "Company", "Role / Job") if not c[f]}

    return contacts, skipped, list(extras)


def render(template, contact, fallbacks):
    """Fill [Placeholders] in a template using contact values or fallbacks."""
    def value(field):
        return contact.get(field) or fallbacks.get(field, "")

    for placeholder, field in PLACEHOLDERS.items():
        template = template.replace(placeholder, value(field))
    for col, val in contact.get("extras", {}).items():
        template = template.replace(f"[{col}]", val or fallbacks.get(col, ""))
    return template


def is_excluded_company(contact, excluded):
    """True if the contact's company or email domain matches any excluded name.

    `excluded` is a comma-separated string, e.g. "Strelema, acme.com". Matching ignores
    case, spaces and punctuation, so "Strelema" also catches anyone@strelema.com.
    """
    domain = contact["Email"].split("@")[-1]
    haystacks = [_norm(contact.get("Company", "")), _norm(domain.rsplit(".", 1)[0])]
    for name in excluded.split(","):
        name = name.strip().lower()
        if name.count(".") and "@" not in name:
            name = name.rsplit(".", 1)[0]  # allow "strelema.com"
        needle = _norm(name)
        if len(needle) >= 3 and any(needle in h for h in haystacks if h):
            return True
    return False


def compose(contact, subject, body, fallbacks):
    """Rendered (subject, body) for a contact; a row's own Subject/Message wins over the defaults."""
    return (render(contact.get("Subject") or subject, contact, fallbacks),
            render(contact.get("Message") or body, contact, fallbacks))
