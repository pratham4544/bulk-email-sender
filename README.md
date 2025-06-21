# Bulk Email Sender

A simple and user-friendly Streamlit application to send bulk emails (with attachments) using Gmail's SMTP server.

---

## Demo

![Bulk Email Sender Screenshot](asset/step1.png)

---

## Features

- Send emails to multiple recipients from a CSV file.
- Attach multiple files to each email.
- Simple web interface built with Streamlit.
- Uses Gmail App Password for secure authentication.

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
   pip install streamlit
   ```

---

## Usage

1. **Start the Streamlit app:**
   ```sh
   streamlit run app.py
   ```

2. **Open the app** in your browser (usually at [http://localhost:8501](http://localhost:8501)).

3. **Fill in the fields:**
   - **Sender Email:** e.g., `demo.sender@gmail.com`
   - **App Password:** e.g., `abcd efgh ijkl mnop` (remove spaces when using)
   - **Email Subject:** e.g., `Welcome to Bulk Email Sender`
   - **Email Body:** e.g., `Hello, this is a test email sent via Bulk Email Sender.`
   - **Upload CSV file:** e.g., `email.csv` (see format below)
   - **Upload attachment(s):** (optional) e.g., `demo.pdf`, `image.png`

4. **Click "Send Emails"** to send emails to all recipients in the CSV.

---

## CSV File Format

The CSV should contain one email address per line, for example:

```
recipient1@example.com
recipient2@example.com
recipient3@example.com
```

A sample [`email.csv`](email.csv) is included.

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

For a detailed guide with screenshots, click the "How to create App Password" button in the app or see [`pages/app_passwords.py`](pages/app_passwords.py).

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
