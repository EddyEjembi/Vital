"""Coach tab — chat with the LLM, tool calls, and quick-question chips."""

import logging

import gradio as gr

from db import queries
from llm.client import get_llm_client
from vital_types.llm import ChatMessage

logger = logging.getLogger(__name__)


def _history_to_messages(history: list[dict[str, str]]) -> list[ChatMessage]:
    """Convert Gradio chat history into LLM conversation messages."""
    messages: list[ChatMessage] = []
    for turn in history:
        role = turn.get("role")
        content = turn.get("content", "")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            messages.append(ChatMessage(role=role, content=content.strip()))
    return messages


def build_coach_tab() -> dict[str, gr.components.Component]:
    """Build the coach chat tab."""
    gr.Markdown("## Coach")
    gr.Markdown(
        "_Ask about your plan, logs, or medications. Vitál can look up your data "
        "and log meals, water, or exercise when you tell it what you did._"
    )

    quick_questions = queries.get_coach_quick_questions()
    default_chip = quick_questions[0] if quick_questions else "How am I doing today?"
    chips = gr.Radio(
        choices=quick_questions if quick_questions else [default_chip],
        label="Quick questions",
        value=default_chip,
    )
    # Gradio 6 removed the `type` kwarg — messages format is the default.
    chatbot = gr.Chatbot(label="Vitál coach", height=400)
    status = gr.Markdown("")
    msg = gr.Textbox(label="Message", placeholder="Ask your coach...")
    with gr.Row():
        send_btn = gr.Button("Send", variant="primary")
        ask_chip_btn = gr.Button("Ask selected question")
        clear_btn = gr.Button("Clear")

    def respond(
        user_message: str,
        history: list[dict[str, str]],
        chip: str,
    ):
        """Send a message to the coach LLM with prior history and tool support."""
        text = user_message.strip() or chip.strip()
        if not text:
            yield history, "", ""
            return

        pending_history = history + [{"role": "user", "content": text}]
        yield (
            pending_history,
            "",
            "⏳ **Vitál is thinking...** This may take a moment...",
        )

        try:
            client = get_llm_client()
            prior_messages = _history_to_messages(history)
            reply = client.chat(
                text,
                extra_messages=prior_messages,
                use_tools=True,
            )
            trimmed_reply = reply.strip() or (
                "I couldn't put together a reply just now. Please try again."
            )
            final_history = pending_history + [
                {"role": "assistant", "content": trimmed_reply},
            ]
            logger.info("[coach] Replied (%s chars) to: %s", len(trimmed_reply), text[:80])
            yield final_history, "", ""
        except Exception as error:
            logger.exception("[coach] Chat failed.")
            yield (
                history,
                text,
                f"**Error:** Could not reach the coach ({error}). Check your LLM connection.",
            )

    respond_inputs = [msg, chatbot, chips]
    respond_outputs = [chatbot, msg, status]

    send_btn.click(
        respond,
        inputs=respond_inputs,
        outputs=respond_outputs,
        show_progress="hidden",
    )
    msg.submit(
        respond,
        inputs=respond_inputs,
        outputs=respond_outputs,
        show_progress="hidden",
    )
    def ask_selected_question(history: list[dict[str, str]], chip: str):
        """Send the currently selected quick-question chip to the coach."""
        yield from respond("", history, chip)

    ask_chip_btn.click(
        ask_selected_question,
        inputs=[chatbot, chips],
        outputs=respond_outputs,
        show_progress="hidden",
    )
    clear_btn.click(lambda: ([], "", ""), outputs=[chatbot, msg, status])

    return {"chatbot": chatbot, "status": status}
