"""Report tab — weekly summary and report history."""

import gradio as gr

from core.weekly_startup import ensure_weekly_report
from db import queries


def _weekly_summary_markdown() -> str:
    """Format weekly summary stats as markdown."""
    summary = queries.get_weekly_summary("current")
    return (
        f"### This week ({summary.get('week_start')} → {summary.get('week_end')})\n\n"
        f"- **Medications:** {summary.get('medications_taken')}/"
        f"{summary.get('medications_total')} taken "
        f"({summary.get('medication_adherence_percent')}%)\n"
        f"- **Exercises completed:** {summary.get('exercises_completed')}\n"
        f"- **Food entries:** {summary.get('food_entries')}\n"
        f"- **Check-in entries:** {summary.get('daily_log_entries')}"
    )


def _latest_report_text() -> str:
    """Show the most recent weekly report narrative if any."""
    report = queries.get_latest_weekly_report()
    if report is None:
        return (
            "_No weekly report generated yet. Reports run automatically on "
            "Sunday evening (from your onboarding schedule), or use **Generate report now** "
            "to test._"
        )
    return f"### Week of {report.week_start.isoformat()}\n\n{report.report_text}"


def build_report_tab() -> dict[str, gr.components.Component]:
    """Build the report tab."""
    gr.Markdown("## Weekly report")
    summary = gr.Markdown(_weekly_summary_markdown())
    narrative = gr.Markdown(_latest_report_text())
    status = gr.Markdown("")
    with gr.Row():
        refresh_btn = gr.Button("Refresh")
        generate_btn = gr.Button("Generate report now", variant="primary")

    def refresh_view() -> tuple:
        """Reload summary and narrative from the database."""
        return (
            gr.update(value=_weekly_summary_markdown()),
            gr.update(value=_latest_report_text()),
            "",
        )

    def generate_now() -> tuple:
        """Force-generate the current week's report for testing."""
        generated = ensure_weekly_report(force=True)
        if generated:
            return (
                gr.update(value=_weekly_summary_markdown()),
                gr.update(value=_latest_report_text()),
                "**Weekly report generated.** Open the narrative below.",
            )
        return (
            gr.update(),
            gr.update(),
            "**Could not generate report.** Check logs and LLM connection.",
        )

    refresh_btn.click(refresh_view, outputs=[summary, narrative, status])
    generate_btn.click(generate_now, outputs=[summary, narrative, status])

    return {"summary": summary, "narrative": narrative}
