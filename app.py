"""TestSmith web app. Run locally with:  streamlit run app.py"""

from __future__ import annotations

import streamlit as st

from ui.session import init_session, load_streamlit_secrets

st.set_page_config(page_title="TestSmith", page_icon=":material/science:", layout="wide")
load_streamlit_secrets()  # must happen before settings are read

from ui.about_tab import render_about_tab  # noqa: E402
from ui.audit_tab import render_audit_tab  # noqa: E402
from ui.results_tabs import render_bugs_tab, render_final_tests_tab  # noqa: E402
from ui.run_tab import render_run_tab  # noqa: E402
from ui.sidebar import render_sidebar  # noqa: E402
from ui.theme import footer, hero, inject_css  # noqa: E402


def main() -> None:
    init_session()
    inject_css()
    options = render_sidebar()
    hero()

    result = st.session_state.result
    bug_count = len(result.state.bugs) if result else 0
    tabs = st.tabs([
        ":material/terminal: Run",
        ":material/code: Final Tests",
        f":material/bug_report: Bugs Found ({bug_count})" if bug_count else ":material/bug_report: Bugs Found",
        ":material/receipt_long: Audit Log",
        ":material/info: About",
    ])
    with tabs[0]:
        render_run_tab(options)
    with tabs[1]:
        render_final_tests_tab(st.session_state.result)
    with tabs[2]:
        render_bugs_tab(st.session_state.result)
    with tabs[3]:
        render_audit_tab()
    with tabs[4]:
        render_about_tab()
    footer()


main()
