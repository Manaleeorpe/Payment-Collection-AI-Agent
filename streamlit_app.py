import os

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

API_BASE = os.getenv("API_BASE", "https://payment-agent-production-37a1.up.railway.app")

st.title("Payment Agent")

if "session_id" not in st.session_state:
    try:
        r = requests.post(f"{API_BASE}/session", timeout=10)
        data = r.json()
        if "session_id" not in data:
            st.error(f"Unexpected response from agent: {data}")
            st.stop()
        st.session_state.session_id = data["session_id"]
        st.session_state.messages = [{"role": "assistant", "content": data["message"]}]
    except requests.ConnectionError:
        st.error(f"Could not connect to agent at {API_BASE}. Is `uvicorn api:app --port 8000` running?")
        st.stop()
    except Exception as e:
        st.error(f"Error: {e}")
        st.stop()

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

if user_input := st.chat_input("Type your message..."):
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.write(user_input)

    r = requests.post(
        f"{API_BASE}/session/{st.session_state.session_id}/message",
        json={"message": user_input},
        timeout=15,
    )
    reply = r.json().get("message", "Something went wrong.")

    st.session_state.messages.append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.write(reply)
