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
    # st.title("Bulk Email Sender")
    
    # Example values for demonstration
    demo_sender = "your.email@gmail.com"
    demo_subject = "Welcome to Bulk Email Sender"
    demo_body = "Hello, this is a test email sent via Bulk Email Sender."
    
    linkedin_url = "https://www.linkedin.com/in/prathameshshete/"

    header_html = f"""
        <div style="
            background: linear-gradient(90deg, #007bff 0%, #00c6ff 100%);
            padding: 22px 32px;
            border-radius: 14px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            box-shadow: 0 4px 16px rgba(0, 123, 255, 0.08);
            margin-bottom: 28px;
        ">
            <div style="display: flex; align-items: center;">
                <img src="https://img.icons8.com/color/48/000000/send-mass-email.png" alt="Mail Icon" style="width:42px;height:42px;margin-right:18px;">
                <span style="font-size:2rem;font-weight:700;color:#fff;letter-spacing:1px;">
                    Bulk Email Sender
                </span>
            </div>
            <a href="{linkedin_url}" target="_blank" style="text-decoration:none;">
                <span style="
                    display: inline-block;
                    padding: 10px 26px;
                    background: #fff;
                    color: #007bff;
                    font-weight: 600;
                    border-radius: 8px;
                    box-shadow: 0 2px 8px rgba(0,123,255,0.10);
                    transition: background 0.2s, color 0.2s;
                    border: 2px solid #007bff;
                " onmouseover="this.style.background='#007bff';this.style.color='#fff';" onmouseout="this.style.background='#fff';this.style.color='#007bff';">
                    <img src="https://img.icons8.com/color/24/000000/linkedin.png" style="vertical-align:middle;margin-right:8px;">Visit My LinkedIn
                </span>
            </a>
        </div>
    """

    st.markdown(header_html, unsafe_allow_html=True)
    
    sender = st.text_input("Sender Email", value=demo_sender)
    st.warning("Don't use your email password. Create an app password.")
    if st.button('How to create App Password'):
        st.switch_page('pages/app_passwords.py')
    password = st.text_input("App Password", type="password")
    subject = st.text_input("Email Subject", value=demo_subject)
    body_message = st.text_area("Email Body", value=demo_body)

    csv_file = st.file_uploader("Upload CSV file with email addresses", type=["csv"])
    
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
