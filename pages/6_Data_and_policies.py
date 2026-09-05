import re
from urllib.parse import quote

import streamlit as st

from auth import account_controls
from settings import setting
from ui import configure_page, navigation


configure_page("Data and policies")
navigation()
account_controls()

st.title("About the data")
st.write("Findings are matched from public researcher metadata and public web pages.")

col1, col2, col3 = st.columns([1, 1, 1])

with col1:
        st.markdown(
                """
                <div style='display:flex;gap:.75rem;align-items:flex-start'>
                    <svg width='44' height='44' viewBox='0 0 24 24' fill='none' xmlns='http://www.w3.org/2000/svg' aria-hidden='true'>
                        <rect width='24' height='24' rx='4' fill='#00274c'/>
                        <path d='M6 13h12M6 9h12M6 17h8' stroke='#ffcb05' stroke-width='1.4' stroke-linecap='round'/>
                    </svg>
                    <div>
                        <strong>Sources & limits</strong>
                        <div style='color:#506274'>We match public researcher metadata and web pages. Results point to original sources — read them before reaching out.</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
        )

with col2:
        st.markdown(
                """
                <div style='display:flex;gap:.75rem;align-items:flex-start'>
                    <svg width='44' height='44' viewBox='0 0 24 24' fill='none' xmlns='http://www.w3.org/2000/svg' aria-hidden='true'>
                        <rect width='24' height='24' rx='4' fill='#00274c'/>
                        <path d='M8 12h8M8 16h8M12 8v8' stroke='#ffcb05' stroke-width='1.4' stroke-linecap='round' stroke-linejoin='round'/>
                    </svg>
                    <div>
                        <strong>Privacy</strong>
                        <div style='color:#506274'>We keep account and submission info for verification and moderation. Do not send sensitive personal data.</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
        )

with col3:
        st.markdown(
                """
                <div style='display:flex;gap:.75rem;align-items:flex-start'>
                    <svg width='44' height='44' viewBox='0 0 24 24' fill='none' xmlns='http://www.w3.org/2000/svg' aria-hidden='true'>
                        <rect width='24' height='24' rx='4' fill='#00274c'/>
                        <path d='M7 12h10M7 8h10M7 16h6' stroke='#ffcb05' stroke-width='1.4' stroke-linecap='round'/>
                    </svg>
                    <div>
                        <strong>Corrections & removal</strong>
                        <div style='color:#506274'>Want something changed or removed? Email <a href='mailto:phdfighting@gmail.com'>phdfighting@gmail.com</a> with the name and URL.</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
        )

st.markdown("---")
st.caption("If you need help with the site or want to report a problem, use the Contact page.")
