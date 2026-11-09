"""Visual layer: CSS, the terminal-style hero, section headers, the coverage meter, and the footer.

Colours follow the dark theme in .streamlit/config.toml. The coverage meter uses one
green ramp (fill + translucent track of the same hue); whether the target is met is
carried by an icon + label, never by colour alone.
"""

from __future__ import annotations

import html

import streamlit as st

INK = "#e6edf3"
MUTED = "#8b949e"
GREEN = "#3fb950"
GREEN_FILL = "#2ea043"
AMBER = "#d29922"

_CSS = """
<style>
/* dotted "graph paper" background */
[data-testid="stAppViewContainer"] {
  background-image: radial-gradient(rgba(240,246,252,0.045) 1px, transparent 1px);
  background-size: 22px 22px;
}
[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 2.2rem; max-width: 1280px; }

/* hero */
.ts-hero { display: grid; grid-template-columns: 1.05fr 1fr; gap: 28px; align-items: center; margin: 0 0 18px; }
@media (max-width: 900px) { .ts-hero { grid-template-columns: 1fr; } }
.ts-kicker { font-family: "JetBrains Mono", monospace; color: #3fb950; font-size: .82rem; letter-spacing: .04em; }
.ts-title { font-family: "JetBrains Mono", monospace; font-weight: 800; font-size: 3rem; line-height: 1.05;
  margin: .25rem 0 .5rem; color: #e6edf3; }
.ts-title .br { color: #58a6ff; }
.ts-tagline { color: #c9d1d9; font-size: 1.08rem; line-height: 1.55; margin-bottom: 14px; max-width: 34rem; }
.ts-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.ts-chip { font-family: "JetBrains Mono", monospace; font-size: .76rem; color: #c9d1d9; border: 1px solid #30363d;
  background: #161b22; border-radius: 999px; padding: 3px 11px; }
.ts-chip b { color: #3fb950; font-weight: 600; }

/* terminal window */
.ts-term { background: #0b0f14; border: 1px solid #30363d; border-radius: 12px; overflow: hidden;
  box-shadow: 0 18px 50px rgba(0,0,0,.45), 0 0 0 1px rgba(63,185,80,.06); }
.ts-term-bar { display: flex; align-items: center; gap: 7px; padding: 9px 13px; background: #161b22;
  border-bottom: 1px solid #21262d; }
.ts-dot { width: 11px; height: 11px; border-radius: 50%; display: inline-block; }
.ts-term-title { font-family: "JetBrains Mono", monospace; font-size: .72rem; color: #8b949e; margin-left: 8px; }
.ts-term-body { padding: 14px 16px 16px; font-family: "JetBrains Mono", monospace; font-size: .8rem;
  line-height: 1.75; color: #c9d1d9; }
.ts-term-body .ln { white-space: pre; overflow: hidden; text-overflow: ellipsis; }
.ts-term .p { color: #3fb950; } .ts-term .c { color: #8b949e; } .ts-term .k { color: #58a6ff; }
.ts-term .ok { color: #3fb950; } .ts-term .w { color: #d29922; } .ts-term .s { color: #a5d6ff; }
.ts-cursor { display: inline-block; width: 8px; height: 1em; background: #3fb950; vertical-align: -2px;
  animation: ts-blink 1.1s steps(1) infinite; }
@keyframes ts-blink { 50% { opacity: 0; } }

/* pipeline strip */
.ts-pipe { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin: 4px 0 22px;
  font-family: "JetBrains Mono", monospace; font-size: .78rem; color: #8b949e; }
.ts-step { border: 1px solid #30363d; background: rgba(22,27,34,.85); border-radius: 8px; padding: 5px 10px; color: #c9d1d9; }
.ts-step span { color: #3fb950; margin-right: 6px; }
.ts-arrow { color: #484f58; }

/* code-comment section headers */
.ts-section { font-family: "JetBrains Mono", monospace; font-size: .95rem; font-weight: 700; color: #e6edf3;
  margin: .2rem 0 .1rem; }
.ts-section .hash { color: #3fb950; }
.ts-section .num { color: #58a6ff; }
.ts-sub { color: #8b949e; font-size: .85rem; margin: 0 0 .6rem; }

/* stat tiles (st.metric) */
[data-testid="stMetric"] { background: rgba(22,27,34,.9); border: 1px solid #30363d; border-radius: 10px; padding: 12px 14px;
  min-height: 118px; }
.ts-label { font-family: "JetBrains Mono", monospace; font-size: .74rem; color: #8b949e; letter-spacing: .03em;
  margin: 12px 0 4px; }
[data-testid="stMetricValue"] { font-family: "JetBrains Mono", monospace; font-weight: 700; }
[data-testid="stMetricLabel"] p { font-family: "JetBrains Mono", monospace; font-size: .74rem; text-transform: lowercase;
  color: #8b949e; letter-spacing: .02em; }

/* coverage meter */
.ts-meter { background: rgba(22,27,34,.9); border: 1px solid #30363d; border-radius: 10px; padding: 14px 16px 12px; margin: 10px 0 16px; }
.ts-meter-head { display: flex; justify-content: space-between; align-items: baseline;
  font-family: "JetBrains Mono", monospace; }
.ts-meter-label { color: #8b949e; font-size: .78rem; }
.ts-meter-value { color: #e6edf3; font-size: 1.5rem; font-weight: 700; }
.ts-meter-track { position: relative; height: 12px; border-radius: 6px; background: rgba(46,160,67,.16); margin: 10px 0 26px; }
.ts-meter-fill { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 6px; background: #2ea043; }
.ts-meter-target { position: absolute; top: -4px; bottom: -4px; width: 2px; background: #e6edf3; }
.ts-meter-target span { position: absolute; top: 20px; transform: translateX(-50%); white-space: nowrap;
  font-family: "JetBrains Mono", monospace; font-size: .7rem; color: #c9d1d9; }
.ts-meter-foot { font-family: "JetBrains Mono", monospace; font-size: .8rem; }

/* cards, expanders, tabs, buttons */
[data-testid="stExpander"] details { background: rgba(13,17,23,.85); }
[data-testid="stExpander"] summary p { font-family: "JetBrains Mono", monospace; font-size: .86rem; }
.stTabs [data-baseweb="tab-list"] { gap: 4px; border-bottom: 1px solid #21262d; }
.stTabs [data-baseweb="tab"] p { font-family: "JetBrains Mono", monospace; font-size: .86rem; }
.stButton button[kind="primary"] { font-family: "JetBrains Mono", monospace; font-weight: 700; letter-spacing: .02em;
  box-shadow: 0 0 0 1px rgba(63,185,80,.35), 0 6px 18px rgba(35,134,54,.25); }

/* empty state + footer */
.ts-empty { border: 1px dashed #30363d; border-radius: 12px; padding: 22px 24px; background: rgba(13,17,23,.75); margin-top: 8px; }
.ts-empty h4 { font-family: "JetBrains Mono", monospace; margin: 0 0 6px; color: #e6edf3; font-size: 1rem; }
.ts-empty p, .ts-empty li { color: #c9d1d9; font-size: .92rem; }
.ts-empty code { color: #a5d6ff; }
.ts-footer { margin-top: 40px; padding-top: 14px; border-top: 1px solid #21262d; text-align: center;
  font-family: "JetBrains Mono", monospace; font-size: .74rem; color: #6e7681; }
.ts-footer b { color: #8b949e; font-weight: 600; }

/* sidebar brand */
.ts-brand { font-family: "JetBrains Mono", monospace; font-weight: 800; font-size: 1.15rem; color: #e6edf3; }
.ts-brand .br { color: #58a6ff; }
.ts-file { font-family: "JetBrains Mono", monospace; font-size: .72rem; color: #8b949e; margin: 2px 0 18px; }
</style>
"""

_TERMINAL_LINES = [
    '<span class="p">$</span> testsmith generate <span class="s">bank_account.py</span> <span class="k">--target</span> 90',
    '<span class="c"># read source · plan: happy path, edge, error, boundary</span>',
    '<span class="ok">+</span> wrote test_bank_account.py <span class="c">(11 tests)</span>',
    '<span class="w">●</span> run 1  13 passed  coverage  60.0%  <span class="c">← 65-106 uncovered</span>',
    '<span class="ok">+</span> added 19 tests <span class="c">(transfer, interest, statement)</span>',
    '<span class="ok">✓</span> run 2  35 passed  coverage <span class="ok">100.0%</span>  <span class="ok">target met</span>',
    '<span class="p">$</span> <span class="ts-cursor"></span>',
]
# One element per line, joined without newlines: Markdown would otherwise reflow a <pre> block.
_TERMINAL = (
    '<div class="ts-term" aria-hidden="true"><div class="ts-term-bar">'
    '<span class="ts-dot" style="background:#ff5f57"></span><span class="ts-dot" style="background:#febc2e"></span>'
    '<span class="ts-dot" style="background:#28c840"></span><span class="ts-term-title">~/project — testsmith</span>'
    '</div><div class="ts-term-body">'
    + "".join(f'<div class="ln">{line}</div>' for line in _TERMINAL_LINES)
    + "</div></div>"
)


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def hero() -> None:
    st.markdown(
        f"""
<div class="ts-hero">
  <div>
    <div class="ts-kicker">// AI agent for unit tests</div>
    <h1 class="ts-title"><span class="br">&lt;</span>TestSmith<span class="br">/&gt;</span></h1>
    <div class="ts-tagline">Drop in a Python module. TestSmith plans, writes, and <b>actually runs</b> pytest
      suites, reads the coverage report, and keeps refining until your target is met. It even flags real bugs.</div>
    <div class="ts-chips">
      <span class="ts-chip"><b>✓</b> pytest</span><span class="ts-chip"><b>✓</b> coverage.py</span>
      <span class="ts-chip"><b>✓</b> sandboxed runs</span><span class="ts-chip"><b>✓</b> Claude</span>
    </div>
  </div>
  {_TERMINAL}
</div>
<div class="ts-pipe">
  <span class="ts-step"><span>01</span>read</span><span class="ts-arrow">→</span>
  <span class="ts-step"><span>02</span>plan</span><span class="ts-arrow">→</span>
  <span class="ts-step"><span>03</span>write</span><span class="ts-arrow">→</span>
  <span class="ts-step"><span>04</span>run</span><span class="ts-arrow">→</span>
  <span class="ts-step"><span>05</span>refine</span><span class="ts-arrow">↺</span>
  <span class="ts-arrow" style="margin-left:6px">until coverage ≥ target</span>
</div>
""",
        unsafe_allow_html=True,
    )


def section(number: str, title: str, subtitle: str = "") -> None:
    """A header styled like a code comment: `# 01 · source`."""
    sub = f'<div class="ts-sub">{html.escape(subtitle)}</div>' if subtitle else ""
    st.markdown(
        f'<div class="ts-section"><span class="hash">#</span> <span class="num">{html.escape(number)}</span> · '
        f"{html.escape(title)}</div>{sub}",
        unsafe_allow_html=True,
    )


def coverage_meter(value: float, target: int, label: str = "line coverage") -> None:
    """Coverage vs target: one green ramp, a target tick, and an icon + label for the status."""
    value = max(0.0, min(100.0, value))
    met = value >= target
    status = (
        f'<span style="color:{GREEN}">✓ target met</span>'
        if met
        else f'<span style="color:{AMBER}">● {target - value:.1f} pts below target</span>'
    )
    st.markdown(
        f"""
<div class="ts-meter" role="meter" aria-label="{html.escape(label)}" aria-valuemin="0" aria-valuemax="100"
     aria-valuenow="{value:.1f}">
  <div class="ts-meter-head"><span class="ts-meter-label">{html.escape(label)}</span>
    <span class="ts-meter-value">{value:.1f}%</span></div>
  <div class="ts-meter-track">
    <div class="ts-meter-fill" style="width:{value:.1f}%"></div>
    <div class="ts-meter-target" style="left:{target}%"><span>target {target}%</span></div>
  </div>
  <div class="ts-meter-foot">{status}</div>
</div>
""",
        unsafe_allow_html=True,
    )


def empty_state() -> None:
    st.markdown(
        """
<div class="ts-empty">
  <h4>&gt; ready when you are_</h4>
  <p>Pick a sample (try <code>bank_account.py</code>), upload a <code>.py</code> file, or paste code, then hit
  <b>Generate tests</b>. You'll watch each step live:</p>
  <ul>
    <li>a <b>test plan</b>: happy path, edge cases, errors, boundaries</li>
    <li>real <b>pytest runs</b> in a sandbox, with coverage after each one</li>
    <li>the final suite to <b>download</b>, plus any <b>bugs</b> it found in your code</li>
  </ul>
  <p style="color:#8b949e;margin:0">Tip: the <b>Work incrementally</b> example shows the refine loop climbing run by run.</p>
</div>
""",
        unsafe_allow_html=True,
    )


def sidebar_brand() -> None:
    st.markdown('<div class="ts-brand"><span class="br">&lt;</span>TestSmith<span class="br">/&gt;</span></div>'
                '<div class="ts-file">⚙ settings.toml</div>', unsafe_allow_html=True)


def footer() -> None:
    st.markdown(
        '<div class="ts-footer">built by <b>Devaansh Mann</b></div>',
        unsafe_allow_html=True,
    )
