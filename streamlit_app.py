from __future__ import annotations

import os

import requests
import streamlit as st

API_BASE = os.getenv("API_BASE", "https://payment-agent-production-37a1.up.railway.app")

st.set_page_config(page_title="Payment Agent", page_icon="💳", layout="centered")
st.title("💳 Payment Collection Agent")

# ── Session bootstrap ────────────────────────────────────────────────────────

def new_session() -> None:
    try:
        res = requests.post(f"{API_BASE}/session", timeout=10)
        res.raise_for_status()
        data = res.json()
        st.session_state.session_id = data["session_id"]
        st.session_state.messages = [{"role": "assistant", "content": data["message"]}]
        st.session_state.ended = False
    except Exception as e:
        st.error(f"Could not connect to the agent service: {e}")


if "session_id" not in st.session_state:
    new_session()

# ── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### Session")
    if st.button("🔄 Start new session", use_container_width=True):
        new_session()
        st.rerun()
    if st.session_state.get("session_id"):
        st.caption(f"ID: `{st.session_state.session_id[:8]}…`")

# ── Chat history ─────────────────────────────────────────────────────────────

for msg in st.session_state.get("messages", []):
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

# ── Input ────────────────────────────────────────────────────────────────────

TERMINAL_PHRASES = (
    "this session is closed",
    "this session is already complete",
    "transaction id",          # payment_ok
    "session is now closed",
)

if st.session_state.get("ended"):
    st.info("Session ended. Click **Start new session** in the sidebar to begin again.")
elif user_input := st.chat_input("Type your message…"):
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.write(user_input)

    try:
        res = requests.post(
            f"{API_BASE}/session/{st.session_state.session_id}/message",
            json={"message": user_input},
            timeout=15,
        )
        if res.status_code == 404:
            reply = "Session not found. Please start a new session."
            st.session_state.ended = True
        elif not res.ok:
            reply = f"Service error ({res.status_code}). Please try again."
        else:
            reply = res.json().get("message", "…")
            if any(p in reply.lower() for p in TERMINAL_PHRASES):
                st.session_state.ended = True
    except requests.Timeout:
        reply = "The agent took too long to respond. Please try again."
    except Exception as e:
        reply = f"Connection error: {e}"

    st.session_state.messages.append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.write(reply)

    if st.session_state.get("ended"):
        st.rerun()
