import streamlit as st

# Set the title of the app
st.title("How to Create a Gmail App Password")

# Introduction
st.write("""
Follow these steps to create an app-specific password for your Gmail account. 
This will allow you to use Gmail with third-party applications securely.
""")

# Step 1
st.header("Step 1: Sign in to your Google Account")
st.write("""
Go to [Google Account](https://myaccount.google.com/) and sign in with your Gmail account.
""")

# Step 2
st.header("Step 2: Navigate to Security Settings")
st.write("""
1. On the left navigation panel, click on **Security**.
2. Under the section **Signing in to Google**, select **App passwords**.
   If prompted, sign in again.
""")
st.image('./asset/step1.png')

# Step 3
st.header("Step 3: Activate 2 Step Verification")
st.write("""
1. On the left navigation panel, click on **Security**
2. How you sign in to Google.
3. 2 Step Verification.
4. Add Mobile Number and Confirm with OTP. 
""")
st.image("asset/step2.png")

# Step 4
st.header("Step 4: Serach App Password")
st.write("""
1. At the Top of the page, Search Box Serach **App Password**.
""")
st.image("asset/step3.png")


st.header("Step 4: Generate an App Password")
st.write("""
1. At the bottom of the page, click on **Select app** and choose the app you want to generate the password for.
2. Click on **Select device** and choose the device you’re using.
3. Click **Generate**.
4. You will see a 16-character password on your screen. 
   Use this password to sign in to your Gmail account from the app you selected.
""")
st.image("asset/step5.png")


# Note
st.header("Important Note")
st.write("""
Remember to keep this password secure and do not share it. You can revoke the password anytime from the same **App passwords** page.
""")

# Conclusion
st.write("""
You have now successfully created a Gmail app password! Use this password in the application you intended to use it for.
""")


st.header("Use It")
st.write("1. Remove spaces from password & visit link [Bulk Email Sender](https://bulk-email-sender.streamlit.app/)")
st.image("asset/step6.png")