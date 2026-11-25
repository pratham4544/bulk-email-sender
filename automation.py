"""LinkedIn scraping (Apify), contact writing and resume tailoring (Groq or Gemini).

No Streamlit calls here, so it can be tested on its own or run from a background thread.
"""
import csv
import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

APIFY_ACTOR = "harvestapi~linkedin-post-search"
APIFY_BASE = "https://api.apify.com/v2"
# Current models from aistudio.google.com/docs/models (Nov 2026). The first is the default.
GEMINI_MODELS = {
    "gemini-3.5-flash-lite": "Fastest and cheapest",
    "gemini-4-hydrogen": "Everyday tasks, good balance",
    "gemini-4-carbon": "Strongest reasoning, best writing, slower and costlier",
    "gemini-4-neon": "Frontier quality, fast",
    "gemini-4-argon": "Efficient Gemini 4, lower token spend",
}
DEFAULT_GEMINI_MODEL = next(iter(GEMINI_MODELS))

GROQ_MODELS = {
    "openai/gpt-oss-20b": "Fast and cheap",
    "openai/gpt-oss-120b": "Stronger writing, a bit slower",
}
DEFAULT_GROQ_MODEL = next(iter(GROQ_MODELS))
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SORT_OPTIONS = {"Most relevant": "relevance", "Newest first": "date"}
POSTED_OPTIONS = {"Any time": None, "Past 24 hours": "24h", "Past week": "week", "Past month": "month"}

# Columns of the CSV produced by automation. The first six are what contacts.py understands;
# the rest become extra columns (usable as [LinkedIn Post] etc. in the template).
CSV_COLUMNS = ["Email", "Contact Person", "Company", "Role / Job", "Subject", "Message",
               "LinkedIn Post", "Job Description"]

EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


# ---------------------------------------------------------------- Apify

class ApifyError(RuntimeError):
    pass


def start_scrape(token, keywords, max_posts, sort_by="relevance", posted_limit=None):
    """Start the actor and return its run id. Save the id: the run keeps going on Apify even if this app
    stops, and its results can be fetched later with wait_for_scrape without paying again."""
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"searchQueries": keywords, "maxPosts": int(max_posts), "sortBy": sort_by,
               "profileScraperMode": "short", "scrapeReactions": False, "scrapeComments": False}
    if posted_limit:
        payload["postedLimit"] = posted_limit

    r = requests.post(f"{APIFY_BASE}/acts/{APIFY_ACTOR}/runs", json=payload, headers=headers, timeout=30)
    if r.status_code == 401:
        raise ApifyError("Apify rejected the token. Copy it again from Apify → Settings → API & Integrations.")
    if not r.ok:
        raise ApifyError(f"Apify could not start the scraper ({r.status_code}): {r.text[:300]}")
    return r.json()["data"]["id"]


def wait_for_scrape(token, run_id, on_status=None, poll_every=5, timeout=900):
    """Wait for a run (returns at once if it already finished) and return its post items."""
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{APIFY_BASE}/actor-runs/{run_id}", headers=headers, timeout=30)
    if r.status_code == 404:
        raise ApifyError("Apify no longer has that search (results are kept for a limited time). Start a new one.")
    r.raise_for_status()
    run = r.json()["data"]

    started = time.monotonic()
    while run["status"] in ("READY", "RUNNING"):
        if time.monotonic() - started > timeout:
            raise ApifyError("The scraper is taking too long. Try fewer keywords or a lower post limit.")
        if on_status:
            on_status(f"Scraping LinkedIn… ({int(time.monotonic() - started)}s)")
        time.sleep(poll_every)
        r = requests.get(f"{APIFY_BASE}/actor-runs/{run['id']}", headers=headers, timeout=30)
        r.raise_for_status()
        run = r.json()["data"]

    if run["status"] != "SUCCEEDED":
        raise ApifyError(f"The scraper finished with status {run['status']}. Check the run in your Apify console.")

    r = requests.get(f"{APIFY_BASE}/datasets/{run['defaultDatasetId']}/items",
                     params={"clean": "true", "format": "json"}, headers=headers, timeout=60)
    r.raise_for_status()
    return [item for item in r.json() if item.get("type", "post") == "post" and item.get("content")]


def emails_in(text):
    """Unique emails in a post, in order of appearance (trailing dots from sentences removed)."""
    seen, found = set(), []
    for m in EMAIL_IN_TEXT.findall(text or ""):
        email = m.strip(".").strip()
        if email.lower() not in seen:
            seen.add(email.lower())
            found.append(email)
    return found


def posts_with_emails(posts):
    """Keep only posts that mention at least one email; attach the emails as `_emails`."""
    kept = []
    for post in posts:
        found = emails_in(post.get("content", ""))
        if found:
            kept.append({**post, "_emails": found})
    return kept


# ---------------------------------------------------------------- LLM (Groq or Gemini)
#
# `llm` everywhere below is a dict: {"provider": "groq" | "gemini", "api_key": ..., "model": ...}.

class LLMError(RuntimeError):
    pass


def _parse_json(text, provider):
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError) as e:
        raise LLMError(f"{provider} returned something that was not valid JSON. Try again.") from e


def _groq_json(api_key, model, prompt, temperature, effort, retries=3):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"}, "temperature": temperature}
    if effort:
        body["reasoning_effort"] = effort
    headers = {"Authorization": f"Bearer {api_key}"}
    for attempt in range(retries + 1):
        r = requests.post(GROQ_URL, json=body, headers=headers, timeout=120)
        if r.status_code == 429 and attempt < retries:  # rate limit; free tier is tight with parallel calls
            time.sleep(min(float(r.headers.get("retry-after", 2 ** attempt)), 30))
            continue
        if r.status_code == 400 and "reasoning_effort" in body and "reasoning" in r.text.lower():
            body.pop("reasoning_effort")  # model has no reasoning setting
            continue
        break
    if r.status_code == 401:
        raise LLMError("Groq rejected the API key. Create one at console.groq.com/keys.")
    if r.status_code == 429:
        raise LLMError("Groq rate limit reached. Wait a minute or pick a smaller batch of posts.")
    if not r.ok:
        raise LLMError(f"Groq request failed ({r.status_code}): {r.text[:300]}")
    return _parse_json(r.json()["choices"][0]["message"]["content"], "Groq")


def _gemini_json(api_key, model, prompt, temperature, thinking):
    from google import genai
    from google.genai import types

    def call(thinking_level):
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=temperature)
        if thinking_level:
            config.thinking_config = types.ThinkingConfig(thinking_level=thinking_level.upper())
        return genai.Client(api_key=api_key).models.generate_content(model=model, contents=prompt, config=config)

    try:
        try:
            resp = call(thinking)
        except Exception as e:
            if not thinking or "thinking" not in str(e).lower():
                raise
            resp = call(None)  # model doesn't support that thinking level; use its default
    except Exception as e:  # SDK raises several error types; show the message to the user
        msg = str(e)
        if "API_KEY_INVALID" in msg or "API key not valid" in msg:
            raise LLMError("Gemini rejected the API key. Create one at aistudio.google.com/apikey.") from e
        raise LLMError(f"Gemini request failed: {msg[:300]}") from e
    return _parse_json(resp.text, "Gemini")


def llm_json(llm, prompt, temperature=0.4, effort=None):
    """One JSON-mode call to the chosen provider. `effort` ("low", ...) trades quality for speed."""
    if llm["provider"] == "groq":
        return _groq_json(llm["api_key"], llm["model"], prompt, temperature, effort)
    return _gemini_json(llm["api_key"], llm["model"], prompt, temperature, effort)


CONTACT_PROMPT = """You help a job seeker write short, personalised cold emails to people who posted jobs on LinkedIn.

About the job seeker (their resume / background):
<background>
{background}
</background>

Their usual email, for tone and sign-off (do not copy it word for word):
<template>
{template}
</template>

Extra instructions from the job seeker (follow them if present):
<note>
{note}
</note>

Below are LinkedIn posts. For EVERY email address listed under each post, return one object:
- "email": exactly the address given
- "contact_person": the full name of the person to address. Use the post author if the email looks like theirs
  or the post is theirs; use a different name only if the post says to contact that person. Empty string if unsure.
- "company": the hiring company named in the post or the author's headline. Empty string if unknown.
- "role": the job title being hired for, short (e.g. "GenAI Engineer"). Empty string if not a job post.
- "subject": a specific subject line (max 10 words) that mentions the role.
- "message": a plain-text email body, 90-150 words. Greet the person by first name (or "Hi there" if unknown),
  refer to something specific from their post, connect 2-3 concrete points from the background to the role,
  mention the resume is attached, and end with the sign-off from the template. Do not include any URL to the
  post: a line with the post link is added automatically after the greeting. Use short paragraphs separated
  by a blank line, with the sign-off on its own lines. No markdown, no placeholders in square brackets.
  Every claim about the job seeker (skills, years, achievements, leadership) must appear in the background
  above. Never invent or exaggerate. If the background is short, write a shorter email.

Return JSON: {{"contacts": [ ... ]}}

Posts:
{posts}
"""


def _post_block(i, post, max_chars=2500):
    author = post.get("author") or {}
    return (f"--- Post {i} ---\n"
            f"Author: {author.get('name', '')}\nAuthor headline: {author.get('info', '')}\n"
            f"Emails: {', '.join(post['_emails'])}\n"
            f"Text:\n{(post.get('content') or '')[:max_chars]}\n")


def build_contacts(llm, posts, background, template, note="", batch_size=4, workers=4, on_status=None,
                   on_rows=None):
    """Turn scraped posts (with `_emails`) into contact rows ready for CSV.

    Batches go to the LLM in parallel; `on_status` and `on_rows` are only called from the caller's thread.
    `on_rows(rows)` gets each batch's rows as soon as it is written, so they can be saved before the rest.
    Returns (rows, errors): rows follow CSV_COLUMNS; errors lists batches that failed. Posts of failed
    batches have no rows (see fallback_rows).
    """
    batches = [posts[i:i + batch_size] for i in range(0, len(posts), batch_size)]

    def write(batch):
        prompt = CONTACT_PROMPT.format(
            background=background.strip() or "(not provided)", template=template.strip() or "(not provided)",
            note=note.strip() or "(none)", posts="\n".join(_post_block(i, p) for i, p in enumerate(batch, 1)))
        return llm_json(llm, prompt, effort="low")

    results, errors = {}, []
    if on_status:
        on_status(f"Writing emails with AI… 0 of {len(posts)} posts")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(write, b): i for i, b in enumerate(batches)}
        done_posts = 0
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                results[i] = fut.result()
                if on_rows:
                    on_rows(_batch_rows(batches[i], results[i]))
            except LLMError as e:
                errors.append(str(e))
                if "API key" in str(e):
                    for f in futures:
                        f.cancel()  # every later batch would fail the same way
                    break
            done_posts += len(batches[i])
            if on_status:
                on_status(f"Writing emails with AI… {done_posts} of {len(posts)} posts")

    rows = []
    for i in sorted(results):  # original order, whatever order the batches finished in
        rows += _batch_rows(batches[i], results[i])
    return dedupe(rows), errors


def _batch_rows(batch, result):
    by_email = {str(c.get("email", "")).strip().lower(): c for c in result.get("contacts", [])}
    rows = []
    for post in batch:
        for email in post["_emails"]:
            c = by_email.get(email.lower(), {})
            rows.append({
                "Email": email,
                "Contact Person": str(c.get("contact_person", "")).strip(),
                "Company": str(c.get("company", "")).strip(),
                "Role / Job": str(c.get("role", "")).strip(),
                "Subject": str(c.get("subject", "")).strip(),
                "Message": add_post_reference(str(c.get("message", "")).strip(), post.get("linkedinUrl", "")),
                "LinkedIn Post": post.get("linkedinUrl", ""),
                "Job Description": (post.get("content") or "").strip(),
            })
    return rows


def dedupe(rows):
    """First row per email address (case-insensitive), order kept."""
    seen, out = set(), []
    for r in rows:
        if r["Email"].lower() not in seen:
            seen.add(r["Email"].lower())
            out.append(r)
    return out


def add_post_reference(message, url):
    """Put a line with the LinkedIn post link after the greeting, so the reader knows which post this is about."""
    if not message or not url or url in message:
        return message
    paragraphs = message.split("\n\n")
    line = f"I'm writing about your LinkedIn post: {url}"
    at = 1 if len(paragraphs) > 1 and len(paragraphs[0]) < 60 else 0  # after a short "Hi Priya," line
    return "\n\n".join(paragraphs[:at] + [line] + paragraphs[at:])


def fallback_rows(posts, template_body, done_emails=()):
    """Rows for posts the AI could not write: blank subject (the default subject is used) and the
    user's own email text with the post link added."""
    done = {e.lower() for e in done_emails}
    rows = []
    for post in posts:
        for email in post["_emails"]:
            if email.lower() not in done:
                rows.append({"Email": email, "Contact Person": (post.get("author") or {}).get("name", ""),
                             "Company": "", "Role / Job": "", "Subject": "",
                             "Message": add_post_reference(template_body, post.get("linkedinUrl", "")),
                             "LinkedIn Post": post.get("linkedinUrl", ""),
                             "Job Description": (post.get("content") or "").strip()})
    return rows


def slim_post(post):
    """Only what the app needs from a scraped post, small enough to store."""
    author = post.get("author") or {}
    return {"author": {"name": author.get("name", ""), "info": author.get("info", "")},
            "content": (post.get("content") or "")[:6000], "linkedinUrl": post.get("linkedinUrl", ""),
            "_emails": post["_emails"]}


def rows_to_csv(rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


# ---------------------------------------------------------------- Resume tailoring

def resume_text(filename, data):
    """Plain text from a PDF, DOCX or TXT resume."""
    name = filename.lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages).strip()
    if name.endswith(".docx"):
        import docx
        return "\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs).strip()
    if name.endswith((".txt", ".md")):
        return data.decode("utf-8", errors="replace").strip()
    raise ValueError("Resume must be a PDF, DOCX or TXT file.")


TAILOR_PROMPT = """You tailor a resume to a job description. Rules:
- Use ONLY facts in the original resume. Never invent jobs, degrees, dates, numbers, tools or skills.
- You may reorder sections and bullets, rewrite the summary, and reword bullets so the most relevant
  experience and the job's keywords (where truthfully supported) come first.
- Keep it to about one page. Keep the candidate's name and contact details exactly as given.

Return JSON:
{{"name": "...",
  "contact": "one line: email | phone | location | links",
  "sections": [
    {{"title": "Summary", "paragraph": "..."}},
    {{"title": "Skills", "bullets": ["Category: item, item", "..."]}},
    {{"title": "Experience", "items": [
       {{"heading": "Job title, Company", "meta": "Dates | Location", "bullets": ["...", "..."]}}]}}
  ]}}
Each section has a "title" and any of "paragraph", "bullets", "items".

Job description:
<jd>
{jd}
</jd>

Original resume:
<resume>
{resume}
</resume>
"""


def tailor_resume(llm, resume, job_description, extra_note=""):
    """Ask the LLM for a tailored resume; returns the structured dict described in TAILOR_PROMPT."""
    prompt = TAILOR_PROMPT.format(jd=job_description[:6000], resume=resume[:12000])
    if extra_note.strip():
        prompt += f"\nExtra instructions from the candidate:\n{extra_note.strip()}\n"
    data = llm_json(llm, prompt, temperature=0.3)
    if not isinstance(data, dict) or not data.get("sections"):
        raise LLMError("The AI returned an empty resume. Try again.")
    return data


_LATIN1_FIXES = str.maketrans({"–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"',
                               "•": "-", "…": "...", " ": " ", "→": "->", "·": "-"})


def _pdf_text(text):
    """The built-in PDF fonts only cover Latin-1, so map common symbols and drop the rest."""
    return str(text or "").translate(_LATIN1_FIXES).encode("latin-1", errors="ignore").decode("latin-1")


def resume_pdf(resume):
    """Render the tailored resume dict to PDF bytes."""
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_margins(15, 12, 15)
    pdf.set_auto_page_break(True, margin=12)
    pdf.add_page()
    width = pdf.w - pdf.l_margin - pdf.r_margin

    def para(text, size=10, style="", gap=0.5, indent=0):
        pdf.set_font("Helvetica", style, size)
        pdf.set_x(pdf.l_margin + indent)
        pdf.multi_cell(width - indent, size * 0.45, _pdf_text(text), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(gap)

    def bullets(items):
        for b in items or []:
            para(f"- {b}", indent=3)

    para(resume.get("name", ""), size=18, style="B", gap=1)
    if resume.get("contact"):
        para(resume["contact"], size=9, gap=2)

    for section in resume.get("sections", []):
        pdf.ln(1.5)
        para(str(section.get("title", "")).upper(), size=11, style="B", gap=0)
        y = pdf.get_y()
        pdf.line(pdf.l_margin, y, pdf.l_margin + width, y)
        pdf.ln(1.5)
        if section.get("paragraph"):
            para(section["paragraph"], gap=1)
        bullets(section.get("bullets"))
        for item in section.get("items") or []:
            if item.get("heading"):
                para(item["heading"], style="B", gap=0)
            if item.get("meta"):
                para(item["meta"], size=9, style="I", gap=0.5)
            bullets(item.get("bullets"))
            pdf.ln(1)
    return bytes(pdf.output())


def resume_filename(resume, company):
    name = re.sub(r"[^A-Za-z0-9]+", "_", resume.get("name", "") or "Resume").strip("_")
    company = re.sub(r"[^A-Za-z0-9]+", "_", company or "").strip("_")
    return f"{name}_Resume" + (f"_{company}" if company else "") + ".pdf"
