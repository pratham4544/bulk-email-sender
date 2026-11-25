# Bulk Email Sender

A simple and user-friendly Streamlit application to send bulk emails (with attachments) using Gmail's SMTP server.

---

## Features

- Send personalised emails to multiple recipients from a CSV file.
- Downloadable template CSV for first-time users.
- Placeholders like `[Recruiter Name]`, `[Company]`, `[Role]` filled per recipient.
- Blank or messy cells fall back to sensible defaults (e.g. "Dear Hiring Manager").
- Skip your current company: type its name (or domain) and matching contacts are not emailed.
- Review/edit the contact table, preview each email, and send a test email to yourself.
- Schedule sending for a later date and time.
- Progress bar, delay between emails, and a downloadable send report.
- Attach multiple files to each email.
- Simple web interface built with Streamlit.
- Uses Gmail App Password for secure authentication.
- Profiles (MongoDB): log in to save your Gmail address, app password, API keys, subject and body.
- Send history: addresses you already emailed are unticked automatically; browse history by last/older than 7, 15 or 30 days.
- Follow-ups page: contacts with no reply after 7/15/30 days, follow-up sent as a reply in the same thread.

---

## Prerequisites

- Python 3.7 or higher
- A Gmail account with [App Password](https://support.google.com/accounts/answer/185833?hl=en) enabled
- Less secure app access is **not** required (App Password is safer)

---

## Installation

1. **Clone the repository:**
   ```sh
   git clone https://github.com/demo-user/bulk-email-sender.git
   cd bulk-email-sender
   ```

2. **Install dependencies:**
   ```sh
   pip install -r requirements.txt
   ```

---

## Usage

1. **Start the Streamlit app:**
   ```sh
   streamlit run app.py
   ```

2. **Open the app** in your browser (usually at [http://localhost:8501](http://localhost:8501)).

3. **Fill in three things and send:**
   1. Your **Gmail address** and **App password**.
   2. Upload your **contacts CSV** and **resume** (optionally type your current company to skip it).
   3. Check the preview, then click **Send**. Use **Test to myself** first if you like.

   To send later, switch **Send now** to **Schedule**, pick a date and time (in your timezone) and click
   **Schedule**. You can close the tab; status, cancel and the report appear under step 1. The app must keep
   running until then (keep the terminal open and the laptop awake when running locally); a restart drops the job.

   Optional sections (collapsed by default): *Review recipients*, *Edit email text*, *Settings* (fallbacks, delay).

4. **Skip typing every time:** register a profile (see below). Your details are filled in on every visit.

---

## Profiles, history and follow-ups

Put these in `.streamlit/secrets.toml` (or `.env` as `NAME=value` lines) locally, and in Streamlit Cloud →
Settings → Secrets when deployed (`.env` is not uploaded). See [`.streamlit/secrets.toml.example`](.streamlit/secrets.toml.example).

| Name | What for |
|---|---|
| `MONGODB_URI` | Profiles, send history, saved searches. In Atlas allow `0.0.0.0/0` under *Network Access*. |
| `APIFY_TOKEN`, `GROQ_API_KEY`, `GEMINI_API_KEY` | The app's shared keys, used when a user hasn't added their own. Never shown to users; usage is billed to you. |

Without `MONGODB_URI` the app runs in guest mode only.

- **Start screen:** *Log in*, *Register* (Gmail, password, resume) or *Continue as guest*. "Keep me logged in"
  remembers the login in that browser for 30 days. There is **no password reset**: a forgotten password means
  registering again.
- **Profile setup** (opens after registering): Gmail app password, resume, LinkedIn search keywords, note for the
  AI (portfolio links etc.), fallback email, and optionally your own Apify/Groq/Gemini keys. Press **Save**.
  Everything is filled in on every visit.
- The login password is stored only as a hash; the profile (keys included) and resume are encrypted with a key
  made from it, so the database alone doesn't reveal them.
- **Main page** for a logged-in user: keywords → *Find contacts and write emails* → review → *Send*.
  CSV upload, resume tailoring, AI model, fallback email text and other settings are under **Advanced**.
- **Searches are saved step by step.** The Apify run id is saved before waiting, the posts once scraped, and every
  batch of emails as soon as the AI writes it. If anything fails or the tab closes, *Continue last search* picks
  up where it stopped without paying Apify again. Contacts the AI couldn't write get your fallback email
  (*Retry AI* tries again).
- Each AI-written email gets a line with the LinkedIn post link after the greeting, so the reader knows which post
  it's about. The subject stays clean.
- **No repeat emails:** contacts you emailed in the last 30 days are listed above the recipients and skipped;
  tick *Email them again anyway* to include them. Contacts emailed more than 30 days ago are included again
  without asking.
- **History** page: filter by last / older than 7, 15 or 30 days, search, download CSV.
- **Follow-ups** page: contacts with no reply after 7, 15 or 30 days; send the follow-up as a reply in the same
  thread, or mark them as replied.
- Every key field has a button that opens the exact page where that key is created (Google app passwords,
  Apify integrations, Groq keys, Google AI Studio).

## Automation (optional)

LinkedIn search is the default way to find contacts. Under **Advanced**: switch to your own CSV, turn on resume tailoring, and pick the AI (**Use Groq**, on by default: Groq with `openai/gpt-oss-20b`, or Gemini when off). Keys come from the user's profile, or the app's shared keys (`APIFY_TOKEN`, `GROQ_API_KEY`, `GEMINI_API_KEY`).

**Find contacts on LinkedIn**: replaces the CSV upload with a search form.
1. Nothing to enter if the app's shared keys are set. Otherwise add your own Apify and Groq (or Gemini) keys in your profile (guests: under *Your own API keys*).
2. Add search keywords (one search per line), max posts per search, sort order (most relevant / newest) and how recent the posts must be.
3. Optionally add a note for the AI (e.g. "mention my 30-day notice period").
4. Press **Find contacts and write emails**. The [harvestapi/linkedin-post-search](https://apify.com/harvestapi/linkedin-post-search) actor scrapes the posts, emails are pulled from the post text, and the AI writes a personal subject and message for each address using your resume and email template.
5. The result goes through the same review table as a CSV and can be downloaded. It adds `LinkedIn Post` and `Job Description` columns, so `[LinkedIn Post]` works as a placeholder.

**Tailor resume to each job**: upload a base resume (PDF/DOCX/TXT), press **Tailor resumes**, and the AI rewrites it for each contact's job description (the LinkedIn post, or the Role column for a manual CSV). It only reorders and rewords what is already in your resume. Each email gets its own tailored PDF; contacts without a job description get the base resume. The PDF uses a simple layout, so check one with **Check tailored resume** before sending.

The model can be picked under **Advanced**. Groq: `openai/gpt-oss-20b` (default) or `openai/gpt-oss-120b`. Gemini: `gemini-3.5-flash-lite` (default, fastest), `gemini-4-hydrogen`, `gemini-4-carbon`, `gemini-4-neon` or `gemini-4-argon`. You can also type another model id. Apify charges per scraped post; check your Apify balance.

## CSV File Format

Click **Template CSV** in the app (or use [`templates/contacts_template.csv`](templates/contacts_template.csv)):

```
Email,Contact Person,Company,Role / Job,Subject,Message
priya.sharma@example.com,Priya Sharma,Example Technologies,AI/ML Engineer,,
hr@example.org,,Example Labs,Data Scientist,,
rahul.verma@example.net,Rahul Verma,,GenAI Engineer,Quick question about the [Role] opening,"Hi [First Name], ..."
```

- Only **Email** is required; other cells may be blank.
- **Subject** and **Message** are optional: fill them to write a custom email for that row (placeholders work).
  Leave them blank to use the default subject/body from the app. For line breaks, press Alt+Enter in Excel /
  Google Sheets, or type `\n`.
- Any extra column (e.g. `Phone`) can be used as a `[Phone]` placeholder.
- A plain list of emails without a header (like [`email.csv`](email.csv)) still works.

### Placeholders and fallbacks

| Placeholder | Column | Used when blank |
|---|---|---|
| `[Recruiter Name]` | Contact Person | Hiring Manager |
| `[First Name]` | Contact Person (first word) | Hiring Manager |
| `[Company]` | Company | your organization |
| `[Role]` | Role / Job | AI/ML Engineer roles |
| `[Email]` | Email | – |

Fallbacks can be changed in the app. Values are also cleaned: emojis and hashtags are removed, ALL-CAPS text is softened,
a Company equal to the Contact Person is treated as blank, and a name repeated across 3+ different email domains
is treated as unreliable and replaced with the fallback.

The default email body lives in [`templates/email_template.txt`](templates/email_template.txt).

---

## Example

Suppose you want to send a welcome email with a PDF attachment to three users:

- **Sender Email:** `demo.sender@gmail.com`
- **App Password:** `abcd efgh ijkl mnop`
- **Subject:** `Welcome!`
- **Body:** `Hi there, welcome to our service. Please find the attached document.`
- **CSV:**  
  ```
  user1@example.com
  user2@example.com
  user3@example.com
  ```
- **Attachment:** `welcome.pdf`

After filling these in and clicking "Send Emails", each user will receive the email with the attachment.

---

## How to Create a Gmail App Password

1. Go to your [Google Account Security page](https://myaccount.google.com/security).
2. Enable 2-Step Verification.
3. Under "Signing in to Google", select **App passwords**.
4. Generate a new app password for "Mail" and "Other (Custom name)".
5. Copy the 16-character password (remove spaces when entering in the app).

In the app, the **Create app password** button next to the field opens [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) directly.

---

## Security Note

- **Never use your main Gmail password.** Always use an App Password.
- App Passwords can be revoked at any time from your Google Account.

---

## License

MIT License

---

## Author

Developed by [Demo User](https://github.com/demo-user)
