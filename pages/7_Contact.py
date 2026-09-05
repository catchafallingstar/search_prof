import os
import streamlit as st
import requests
from urllib.parse import quote

from auth import account_controls
from ui import configure_page, navigation


configure_page("Contact")
navigation()
account_controls()

st.title("Contact")
st.write("Have a question, correction, or need something removed? Send a short message below.")

col1, col2 = st.columns([2, 1])

with col1:
    name = st.text_input("Your name")
    email = st.text_input("Your email")
    message = st.text_area("Message", "Please include the name and URL of the item to correct or remove.")
    send = st.button("Send", type="primary")
    if send:
        if not name.strip() or not email.strip():
            st.error("Name and email are required")
        else:
                            # Prefer Web3Forms (no server required). Set WEB3FORMS_ACCESS_KEY in environment.
                            web3_key = os.getenv("WEB3FORMS_ACCESS_KEY") or os.getenv("CONTACT_API_KEY")
                            if web3_key:
                                try:
                                    payload = {
                                        "access_key": web3_key,
                                        "name": name,
                                        "email": email,
                                        "subject": "ScholarRadar contact",
                                        "message": message,
                                        "botcheck": "",
                                    }
                                    resp = requests.post("https://api.web3forms.com/submit", json=payload, timeout=10)
                                except requests.RequestException as e:
                                    st.error(f"Failed to reach Web3Forms: {e}")
                                else:
                                    try:
                                        data = resp.json()
                                    except Exception:
                                        data = None
                                    if resp.ok and (data is None or data.get("success", True)):
                                        st.success("Message sent — we will respond as soon as possible.")
                                    else:
                                        err = (data and data.get("message")) or resp.text
                                        st.error(f"Failed to send message: {err}")
                            else:
                                # Fallback to mailto link if no API key configured
                                st.warning("No contact backend configured. Sending via your mail app instead.")
                                subject = quote("ScholarRadar contact")
                                body_lines = [f"Name: {name}", f"Email: {email}", "", message]
                                body = quote("\n".join(body_lines))
                                st.markdown(f"[Send via email](mailto:phdfighting@gmail.com?subject={subject}&body={body})")

with col2:
    st.markdown(
        """
        <div style='text-align:center'>
          <svg width='96' height='96' viewBox='0 0 24 24' fill='none' xmlns='http://www.w3.org/2000/svg' aria-hidden='true'>
            <rect width='24' height='24' rx='5' fill='#00274c'/>
            <path d='M4 7h16v10H4z' stroke='#ffcb05' stroke-width='1.2' stroke-linecap='round' stroke-linejoin='round'/>
            <path d='M4 7l8 6 8-6' stroke='#ffcb05' stroke-width='1.2' stroke-linecap='round' stroke-linejoin='round'/>
          </svg>
          <div style='margin-top:.5rem'>Email: <a href='mailto:phdfighting@gmail.com'>phdfighting@gmail.com</a></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.caption("We will respond to messages as soon as possible.")
