import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import taxonomy as T
from core.config import JUDGES
from core.scheduler import build_causelist
from pages.common import resources, sidebar

conn, predictor = resources()
jid, day = sidebar()

st.title("Case types and judge time")
st.caption("Ten kinds of High Court case (a to j), each with sub-types. How long each hearing takes depends on "
           "the type, the sub-type, the stage, the size of the paper book, the parties and whether a summary was "
           "verified. Every number comes from config/case_taxonomy.yaml.")

cat = T.catalogue()
tabs = st.tabs(["The ten types", "Minutes per sub-type", "Cost of waiting", "The judge's day", "What the judge checks"])

# ---------------------------------------------------------------- 1. the ten types
with tabs[0]:
    rows = []
    for n, (tk, t) in enumerate(T.TYPES.items()):
        c = cat[cat.type == tk]
        adm, arg = c[c.stage == "admission"], c[c.stage == "arguments"]
        rows.append({"": "abcdefghij"[n], "Type": t["name"], "Code": t["code"], "Law": t["statute"],
                     "Sub-types": len(t["subtypes"]),
                     "Admission, min": f"{adm.median_min.min():.0f}-{adm.median_min.max():.0f}",
                     "Arguments, min": f"{arg.median_min.min():.0f}-{arg.median_min.max():.0f}",
                     "Liberty at stake": "Yes" if t["liberty"] else "",
                     "Waiting weight": t["value"]["weight"]})
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    st.markdown("**How long a hearing takes**")
    st.latex(r"\text{minutes} = m_{\text{type,stage}} \times k_{\text{sub-type}} \times "
             r"\left(\tfrac{\text{pages}}{100}\right)^{0.2} \times (1 + 0.08\,(\text{parties}-2)) "
             r"\times c_{\text{cover sheet}} \times c_{\text{pre-checked}} \times \varepsilon,\quad "
             r"\varepsilon \sim \text{LogNormal}(0, \sigma_{\text{type}})")
    st.caption("Against the purpose-only reference table, this model cuts the error in predicted hearing length "
               "from 11.8 to 5.5 minutes on held-out hearings (Model accuracy page).")
    st.markdown("**Evidence behind the relative times**")
    st.markdown("No Indian source publishes minutes per High Court hearing by case type. Official unit norms for "
                "judges do give relative judge time, and our multipliers follow them: a contested bail is 0.2 of a "
                "judge-day and a criminal appeal against conviction 2.0 in Maharashtra's norms, the same tenfold gap "
                "as our 6-minute bail admission and 60-minute appeal argument. Weighted-caseload studies abroad show "
                "spreads of over 100 times between case types.")
    st.dataframe(pd.DataFrame(T.TAX["sources"]).rename(columns={"what": "Finding", "who": "Source", "url": "Link"}),
                 width="stretch", hide_index=True, column_config={"Link": st.column_config.LinkColumn()})
    st.caption("Minutes are estimates to calibrate against the organisers' hearing data: replace stage_minutes and "
               "the multipliers in config/case_taxonomy.yaml, nothing else changes.")

# ---------------------------------------------------------------- 2. minutes per sub-type
with tabs[1]:
    c1, c2 = st.columns([1, 1])
    tk = c1.selectbox("Type", list(T.TYPES), format_func=lambda k: T.TYPES[k]["name"])
    stage = c2.segmented_control("Stage", T.STAGES, default="admission")
    stage = stage or "admission"
    sub = cat[(cat.type == tk) & (cat.stage == stage)].copy()
    sigma = T.TYPES[tk]["sigma"]
    sub["p10"] = sub.median_min * np.exp(-1.2816 * sigma)
    sub = sub.sort_values("median_min")
    fig = go.Figure(go.Scatter(
        x=sub.median_min, y=sub.subtype_name, mode="markers", marker=dict(size=10, color="#2a78d6"),
        error_x=dict(type="data", symmetric=False, array=sub.p90_min - sub.median_min,
                     arrayminus=sub.median_min - sub.p10, color="#9a9893", thickness=2),
        hovertemplate="%{y}<br>median %{x:.1f} min<extra></extra>"))
    fig.update_layout(height=60 + 42 * len(sub), margin=dict(l=0, r=0, t=10, b=0), yaxis=dict(automargin=True),
                      xaxis_title=f"Minutes per {stage} hearing (dot: median; line: 10th to 90th percentile)")
    st.plotly_chart(fig, width="stretch")

    st.markdown("**Try one case**")
    a, b, c, d, e = st.columns(5)
    sk = a.selectbox("Sub-type", list(T.subtypes(tk)), format_func=lambda k: T.subtypes(tk)[k]["name"])
    pages = b.number_input("Paper book, pages", 10, 3000, 100, 10)
    parties = c.number_input("Parties", 2, 20, 2)
    cover = d.toggle("Cover sheet verified")
    pre = e.toggle("Documents pre-checked")
    med = T.median_minutes(tk, sk, stage, pages=pages, parties=parties, cover_sheet=cover, prechecked=pre)
    base = T.median_minutes(tk, sk, stage)
    st.metric("Expected minutes (median)", f"{med:.1f}", f"{med - base:+.1f} vs a plain 100-page, two-party case",
              delta_color="inverse")

# ---------------------------------------------------------------- 3. cost of waiting
with tabs[2]:
    st.markdown("Each day a case waits costs something, and the cost grows. Liberty matters grow fastest and "
                "jump when a statutory clock runs out (default bail after 60 days). The planner adds this cost "
                "to a case's priority, so what is listed first follows the cost of delay.")
    pick = st.multiselect("Compare", list(T.TYPES), default=["a_bail", "f_habeas", "e_writ_civil", "g_civil_appeal"],
                          format_func=lambda k: T.TYPES[k]["name"], max_selections=4)
    days = np.arange(0, 731)
    colors = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
    fig = go.Figure()
    for k, col in zip(pick, colors):
        fig.add_scatter(x=days, y=T.waiting_cost(k, days), name=T.TYPES[k]["name"], line=dict(color=col, width=2),
                        hovertemplate="day %{x}: cost %{y:.0f}<extra>" + T.TYPES[k]["name"] + "</extra>")
    fig.update_layout(height=340, margin=dict(l=0, r=0, t=10, b=0), hovermode="x unified",
                      legend=dict(orientation="h", y=-0.2), xaxis_title="Days waiting", yaxis_title="Cost per day")
    st.plotly_chart(fig, width="stretch")
    st.latex(r"\text{cost}(t) = w_{\text{type}} \left(1 + g_{\text{type}} \tfrac{t}{30}\right) \times "
             r"\begin{cases} c & t \ge \text{clock} \\ 1 & \text{otherwise}\end{cases}")
    st.dataframe(pd.DataFrame([{"Type": t["name"], "Weight w": t["value"]["weight"], "Growth g per month":
                                t["value"]["growth"], "Clock, days": t["value"]["clock_days"],
                                "Multiplier past clock": t["value"]["clock_multiplier"]} for t in T.TYPES.values()]),
                 width="stretch", hide_index=True)

# ---------------------------------------------------------------- 4. the judge's day
with tabs[3]:
    r = build_causelist(conn, predictor, jid, day)
    items = r["items"]
    st.markdown(f"**{JUDGES[jid]['name']}, {day:%d %b}: where every minute goes**")
    if items.empty:
        st.info("No list for this day.")
    else:
        kinds = items.category.fillna(items.next_purpose)
        seq_items = items.assign(type=kinds, minutes=items.expected_minutes,
                                 weight=(items.priority.clip(upper=1100) / 10).round())
        naive_sw, grouped_sw, naive_min, grouped_min = 0, 0, 0, 0
        for _, g in seq_items.groupby("block"):
            nv = T.naive_changeover(g)
            _, info = T.sequence_with_changeovers(g, time_limit=5)
            naive_sw += nv["switches"]
            grouped_sw += info["switches"]
            naive_min += nv["changeover"]
            grouped_min += info["changeover"]
        bud = T.day_budget(jid, len(items), grouped_sw)
        hearing = items.expected_minutes.sum() - len(items) * T.DAY["changeover_same_type"]
        steps = [("Court day, first call to rising", bud["court_span"], "absolute"),
                 ("Lunch", -bud["lunch"], "relative"),
                 ("Pronouncements and mentions", -bud["opening"], "relative"),
                 ("Short breaks", -bud["misc_breaks"], "relative"),
                 ("Changeovers between cases", -bud["changeovers"], "relative"),
                 ("Planned hearing time", -hearing, "relative"),
                 ("Left free", None, "total")]
        fig = go.Figure(go.Waterfall(
            orientation="v", measure=[s[2] for s in steps], x=[s[0] for s in steps],
            y=[s[1] if s[1] is not None else 0 for s in steps], text=[f"{abs(s[1]):.0f}" if s[1] else "" for s in steps],
            textposition="outside", connector=dict(line=dict(color="#9a9893", width=1)),
            decreasing=dict(marker=dict(color="#eb6834")), increasing=dict(marker=dict(color="#2a78d6")),
            totals=dict(marker=dict(color="#1baf7a"))))
        fig.update_layout(height=360, margin=dict(l=0, r=0, t=20, b=0), yaxis_title="Minutes", showlegend=False)
        st.plotly_chart(fig, width="stretch")
        c1, c2, c3 = st.columns(3)
        c1.metric("Hearings listed", len(items))
        c2.metric("Type switches if called in priority order", naive_sw, f"{naive_min} changeover minutes",
                  delta_color="off", delta_arrow="off")
        c3.metric("Type switches when grouped (CP-SAT)", grouped_sw,
                  f"{grouped_min} changeover minutes, {naive_min - grouped_min} saved", delta_color="off", delta_arrow="off")
        st.caption(f"Changeover: {T.DAY['changeover_same_type']} min between cases of the same kind, "
                   f"{T.DAY['changeover_switch_type']} min when the judge switches to a different kind of case. "
                   "Grouping similar cases cuts context switching, which the manual names as a judge's burden.")

# ---------------------------------------------------------------- 5. what the judge checks
with tabs[4]:
    tk2 = st.selectbox("Type", list(T.TYPES), format_func=lambda k: T.TYPES[k]["name"], key="chk_type")
    t = T.TYPES[tk2]
    sk2 = st.selectbox("Sub-type", list(t["subtypes"]), format_func=lambda k: t["subtypes"][k]["name"], key="chk_sub")
    c1, c2 = st.columns(2)
    c1.markdown("**What the judge checks at the hearing**\n" + "\n".join(f"- {c}" for c in T.all_checks(tk2, sk2)))
    c2.markdown("**Documents the pre-filing check can verify first**\n" + "\n".join(f"- {d}" for d in t["documents"]))
    c2.caption(f"When these are verified before listing, the hearing is planned {100 - T.TAX['precheck_factor'] * 100:.0f}% "
               "shorter: the judge does not spend court time finding them.")
    st.caption(f"Law: {t['statute']}.")
