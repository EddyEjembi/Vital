"""Shared Gradio theme and small CSS polish for Vitál."""

import gradio as gr

VITAL_CSS = """
.gradio-container {
    max-width: 1120px !important;
    margin: 0 auto !important;
}

.vital-header {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 12px 4px 18px;
}
.vital-logo {
    height: 44px;
    width: auto;
    border-radius: 8px;
}
.vital-wordmark {
    font-size: 1.55rem;
    font-weight: 750;
    line-height: 1.05;
}
.vital-tagline {
    opacity: 0.72;
    margin-top: 3px;
    font-size: 0.96rem;
}

.coach-bubble {
    padding: 1rem 1.15rem;
    border-radius: 8px;
    border-left: 4px solid #059669;
    background: #ecfdf5;
    color: #064e3b;
}
.coach-bubble p,
.coach-bubble li,
.coach-bubble strong,
.coach-bubble em {
    color: #064e3b !important;
}
.dark .coach-bubble {
    border-left-color: #34d399;
    background: #13231c;
    color: #e8f7ef;
}
.dark .coach-bubble p,
.dark .coach-bubble li,
.dark .coach-bubble strong,
.dark .coach-bubble em {
    color: #e8f7ef !important;
}

#vital-status {
    padding: 0.75rem 1rem;
    border-radius: 8px;
}

@media (max-width: 720px) {
    .vital-header {
        align-items: flex-start;
    }
    .vital-logo {
        height: 38px;
    }
    .vital-wordmark {
        font-size: 1.35rem;
    }
}
"""


def get_vital_theme() -> gr.Theme:
    """Return a simple readable Gradio theme with explicit dark-mode controls."""
    return gr.themes.Soft(
        primary_hue="emerald",
        secondary_hue="teal",
        neutral_hue="slate",
    ).set(
        body_background_fill="#f7faf7",
        body_background_fill_dark="#0f1720",
        body_text_color="#10231b",
        body_text_color_dark="#eef8f1",
        body_text_color_subdued="#5f7568",
        body_text_color_subdued_dark="#b8c8be",
        block_background_fill="#ffffff",
        block_background_fill_dark="#16221b",
        block_border_color="#d7e5dc",
        block_border_color_dark="#314238",
        block_border_width="1px",
        block_radius="8px",
        block_shadow="none",
        block_shadow_dark="none",
        block_label_background_fill="transparent",
        block_label_background_fill_dark="transparent",
        block_label_border_width="0px",
        block_label_border_width_dark="0px",
        block_label_padding="0 0 0.35rem 0",
        block_label_text_color="#31483a",
        block_label_text_color_dark="#d7e6dd",
        block_label_text_size="0.9rem",
        block_label_text_weight="600",
        input_background_fill="#ffffff",
        input_background_fill_dark="#101a14",
        input_background_fill_focus="#ffffff",
        input_background_fill_focus_dark="#101a14",
        input_border_color="#b8cabc",
        input_border_color_dark="#718477",
        input_border_color_focus="#0ea5e9",
        input_border_color_focus_dark="#38bdf8",
        input_border_width="1px",
        input_border_width_dark="1px",
        input_placeholder_color="#6b8174",
        input_placeholder_color_dark="#9fb2a6",
        input_radius="7px",
        input_shadow="none",
        input_shadow_dark="none",
        input_shadow_focus="0 0 0 2px rgba(14, 165, 233, 0.28)",
        input_shadow_focus_dark="0 0 0 2px rgba(56, 189, 248, 0.30)",
        checkbox_background_color="#ffffff",
        checkbox_background_color_dark="#101a14",
        checkbox_background_color_selected="#059669",
        checkbox_background_color_selected_dark="#34d399",
        checkbox_border_color="#718477",
        checkbox_border_color_dark="#c2d8ca",
        checkbox_border_color_selected="#059669",
        checkbox_border_color_selected_dark="#34d399",
        checkbox_border_width="2px",
        checkbox_border_width_dark="2px",
        checkbox_label_background_fill="transparent",
        checkbox_label_background_fill_dark="transparent",
        checkbox_label_background_fill_selected="#ecfdf5",
        checkbox_label_background_fill_selected_dark="#18352a",
        checkbox_label_border_color="#d7e5dc",
        checkbox_label_border_color_dark="#314238",
        checkbox_label_text_color="#10231b",
        checkbox_label_text_color_dark="#eef8f1",
        checkbox_label_text_color_selected="#064e3b",
        checkbox_label_text_color_selected_dark="#d1fae5",
        button_primary_background_fill="#047857",
        button_primary_background_fill_dark="#34d399",
        button_primary_background_fill_hover="#065f46",
        button_primary_background_fill_hover_dark="#6ee7b7",
        button_primary_text_color="#ffffff",
        button_primary_text_color_dark="#06251c",
        button_secondary_background_fill="#ffffff",
        button_secondary_background_fill_dark="#16221b",
        button_secondary_border_color="#b8cabc",
        button_secondary_border_color_dark="#718477",
        button_secondary_text_color="#10231b",
        button_secondary_text_color_dark="#eef8f1",
        panel_background_fill="#ffffff",
        panel_background_fill_dark="#16221b",
        table_border_color="#d7e5dc",
        table_border_color_dark="#314238",
        table_even_background_fill="#ffffff",
        table_even_background_fill_dark="#16221b",
        table_odd_background_fill="#f7faf7",
        table_odd_background_fill_dark="#101a14",
    )
