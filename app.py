import streamlit as st
import smtplib, ssl, csv
from email.message import EmailMessage
import os

st.set_page_config(
    page_title="Bulk Email Sender",
    page_icon=":mail:",
    layout="centered",
    initial_sidebar_state="collapsed",
)


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
    
    
    linkedin_url = "https://www.linkedin.com/in/prathameshshete/"

    header_html = f"""
        <div style="background-color:#f0f0f0;padding:10px;border-radius:10px;">
            <h1 style="color:#333;"> </h1>
            <a href="{linkedin_url}" target="_blank" style="float:right;text-decoration:none;">
                <button style="padding:5px 10px;background-color:#007bff;color:white;border:none;border-radius:5px;cursor:pointer;">Visit My LinkedIn</button>
            </a>
        </div>
    """

    # Render the header using the HTML component
    st.markdown(header_html, unsafe_allow_html=True)
    

    sender = st.text_input("Sender Email")
    st.warning("don't use you email password it won't work create a app password")
    if st.button('How to create App Password'):
        st.switch_page('pages/app_passwords.py')
    password = st.text_input("App Password")
    subject = st.text_input("Email Subject")
    body_message = st.text_area("Email Body")

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
