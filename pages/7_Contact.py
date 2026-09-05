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
    # Render client-side HTML form if Web3Forms key is configured (avoids server-side restriction).
    web3_key = os.getenv("WEB3FORMS_ACCESS_KEY") or os.getenv("CONTACT_API_KEY")
    if web3_key:
        form_html = f"""
        <form action="https://api.web3forms.com/submit" method="POST" id="sr-contact" style="max-width:720px;">
          <input type="hidden" name="access_key" value="{web3_key}" />
          <input type="hidden" name="subject" value="ScholarRadar contact" />
          <input type="hidden" name="redirect" value="" />
          <div style="display:flex;flex-direction:column;gap:.5rem;margin-bottom:.5rem;">
            <label for="name">Your name</label>
            <input id="name" name="name" type="text" required style="padding:.6rem;border-radius:.35rem;border:1px solid #cbd5e1;" />
          </div>
          <div style="display:flex;flex-direction:column;gap:.5rem;margin-bottom:.5rem;">
            <label for="email">Your email</label>
            <input id="email" name="email" type="email" required style="padding:.6rem;border-radius:.35rem;border:1px solid #cbd5e1;" />
          </div>
          <div style="display:flex;flex-direction:column;gap:.5rem;margin-bottom:.5rem;">
            <label for="message">Message</label>
            <textarea id="message" name="message" placeholder="Please include the name and URL of the item to correct or remove." rows="6" style="padding:.6rem;border-radius:.35rem;border:1px solid #cbd5e1;resize:vertical;"></textarea>
          </div>
          <input type="text" name="botcheck" style="display:none" />
          <button type="submit" style="background:#00274c;color:#fff;padding:.6rem 1rem;border-radius:.45rem;border:none;font-weight:700;">Send</button>
        </form>
        """
        st.markdown(form_html, unsafe_allow_html=True)
    else:
        name = st.text_input("Your name")
        email = st.text_input("Your email")
        message = st.text_area("Message", value="", placeholder="Please include the name and URL of the item to correct or remove.")
        send = st.button("Send", type="primary")
        if send:
            if not name.strip() or not email.strip():
                st.error("Name and email are required")
            else:
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
