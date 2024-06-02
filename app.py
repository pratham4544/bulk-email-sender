import streamlit as st
import smtplib, ssl, csv
from email.message import EmailMessage
import os

def send_bulk_emails(sender, password, subject, body_message, csv_file, attachments):
    context = ssl.create_default_context()
    server = smtplib.SMTP_SSL('smtp.gmail.com', 465, context=context)
    server.login(sender, password)

    with open(csv_file, 'r') as csvfile:
        datareader = csv.reader(csvfile)
        for row in datareader:
            em = EmailMessage()
            em['From'] = sender
            em['To'] = row[0]
            em['Subject'] = subject
            em.set_content(body_message)
            
            # Attach files
            for attachment in attachments:
                em.add_attachment(attachment["content"], maintype=attachment["maintype"], subtype=attachment["subtype"], filename=attachment["filename"])
            
            server.send_message(em)
            st.write(f"Message sent to {row[0]}")

    server.close()
    st.write("All messages sent")

def main():
    st.title("Bulk Email Sender")

    sender = st.text_input("Sender Email", value='youremailid@gmail.com')
    password = st.text_input("App Password", type="password")
    subject = st.text_input("Email Subject", value='Test')
    body_message = st.text_area("Email Body", value='This is the text for demo')

    csv_file = st.file_uploader("Upload CSV file with email addresses", type=["csv"])
    
    # Allow multiple file uploads for attachments
    attachments = st.file_uploader("Upload attachment(s)", type=None, accept_multiple_files=True)

    if st.button("Send Emails"):
        if not sender or not password or not subject or not body_message or not csv_file:
            st.error("Please fill out all fields and upload a CSV file")
        else:
            with open("emails.csv", "wb") as f:
                f.write(csv_file.getbuffer())
            
            attachment_data = []
            for attachment in attachments:
                content = attachment.read()
                maintype, subtype = attachment.type.split('/', 1)
                attachment_data.append({
                    "filename": attachment.name,
                    "content": content,
                    "maintype": maintype,
                    "subtype": subtype
                })
            
            send_bulk_emails(sender, password, subject, body_message, "emails.csv", attachment_data)
            os.remove("emails.csv")

if __name__ == "__main__":
    main()
