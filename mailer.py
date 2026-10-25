"""Gmail sending and scheduled jobs. No Streamlit calls here, so it is safe to run in a background thread."""
import smtplib
import ssl
import threading
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage

from contacts import compose


def build_message(sender, contact, subject, body, attachments, fallbacks):
    em = EmailMessage()
    em['From'] = sender
    em['To'] = contact["Email"]
    em['Subject'], content = compose(contact, subject, body, fallbacks)
    em.set_content(content)

    for attachment in attachments:
        em.add_attachment(attachment["content"], maintype=attachment["maintype"], subtype=attachment["subtype"], filename=attachment["filename"])
    return em


def deliver(sender, password, subject, body, contacts, attachments, fallbacks, delay,
            on_progress=None, stop_event=None):
    """Send one email per contact. Raises smtplib.SMTPAuthenticationError on a bad login.

    Returns a list of {"Email", "Status", "Error"}; one failing address doesn't stop the batch.
    """
    stop_event = stop_event or threading.Event()
    server = smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ssl.create_default_context())
    server.login(sender, password)

    results = []
    try:
        for i, contact in enumerate(contacts, start=1):
            if stop_event.is_set():
                break
            try:
                server.send_message(build_message(sender, contact, subject, body, attachments, fallbacks))
                results.append({"Email": contact["Email"], "Status": "Sent", "Error": ""})
            except Exception as e:
                results.append({"Email": contact["Email"], "Status": "Failed", "Error": str(e)})
            if on_progress:
                on_progress(i, len(contacts), contact["Email"])
            if i < len(contacts) and delay:
                stop_event.wait(delay)
    finally:
        server.close()
    return results


class Scheduler:
    """Holds scheduled jobs for the lifetime of the server process.

    Each job runs in its own daemon thread, so it keeps going after the browser tab closes,
    but is lost if the app/server restarts.
    """

    def __init__(self):
        self.jobs = {}
        self.lock = threading.Lock()

    def schedule(self, send_at, sender, password, subject, body, contacts, attachments, fallbacks, delay):
        job = {
            "id": uuid.uuid4().hex[:8], "sender": sender, "send_at": send_at,
            "total": len(contacts), "done": 0, "status": "Waiting", "error": "", "results": [],
            "cancel": threading.Event(),
        }
        with self.lock:
            self.jobs[job["id"]] = job
        args = (job, password, subject, body, contacts, attachments, fallbacks, delay)
        threading.Thread(target=self._run, args=args, daemon=True).start()
        return job

    def _run(self, job, password, subject, body, contacts, attachments, fallbacks, delay):
        wait = (job["send_at"] - datetime.now(timezone.utc)).total_seconds()
        if wait > 0 and job["cancel"].wait(wait):
            job["status"] = "Cancelled"
            return

        job["status"] = "Sending"

        def progress(i, total, email):
            job["done"] = i

        try:
            job["results"] = deliver(job["sender"], password, subject, body, contacts, attachments,
                                     fallbacks, delay, on_progress=progress, stop_event=job["cancel"])
            job["status"] = "Cancelled" if job["cancel"].is_set() else "Done"
        except smtplib.SMTPAuthenticationError:
            job["status"], job["error"] = "Failed", "Gmail rejected the login (check the app password)."
        except Exception as e:
            job["status"], job["error"] = "Failed", str(e)

    def jobs_for(self, sender):
        with self.lock:
            return sorted((j for j in self.jobs.values() if j["sender"].lower() == sender.lower()),
                          key=lambda j: j["send_at"])

    def cancel(self, job_id):
        self.jobs[job_id]["cancel"].set()
