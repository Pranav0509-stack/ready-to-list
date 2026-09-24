import plotly.graph_objects as go
import streamlit as st

from core import evaluate
from pages.common import resources, sidebar

BLUE, ORANGE, MUTED = "#2a78d6", "#eb6834", "#9a9893"

conn, predictor = resources()
sidebar()


@st.cache_data(show_spinner="Scoring the models on held-out hearings...")
def _holdout(_conn, n_train, test_size=0.25):
    return evaluate.holdout(_conn, test_size=test_size)


@st.cache_data(show_spinner="Measuring accuracy vs amount of history...")
def _learning(_conn, n_train):
    return evaluate.learning_curve(_conn)


@st.cache_data(show_spinner="Back-testing cause lists against simulated outcomes...")
def _backtest(days, seed):
    return evaluate.backtest_causelists(seed_days=days, seed=seed)


def calibration_chart(t, title):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[0, 1], y=[0, 1], mode="lines", name="Perfect calibration",
                             line=dict(color=MUTED, dash="dash", width=2), hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=t.mean_predicted, y=t.observed, mode="lines+markers", name="Model",
                             line=dict(color=BLUE, width=2), marker=dict(size=9, color=BLUE),
                             customdata=t["count"],
                             hovertemplate="Predicted %{x:.0%}<br>Observed %{y:.0%}<br>%{customdata} hearings<extra></extra>"))
    fig.update_layout(title=title, height=340, margin=dict(l=10, r=10, t=50, b=10),
                      xaxis=dict(title="Predicted probability", range=[0, 1], tickformat=".0%"),
                      yaxis=dict(title="Observed rate", range=[0, 1], tickformat=".0%"),
                      legend=dict(orientation="h", y=-0.25))
    return fig


# Keyed on training size so the cache refreshes when new outcomes are written back
n_train = predictor.n_train
ho = _holdout(conn, n_train)
s, e, d = ho["show"], ho["effective"], ho["duration"]

st.title("Model accuracy")
st.caption(f"Trained on hearings up to {ho['train_range'][1]}, scored on the {s['n_test']:,} newest "
           f"hearings ({ho['test_range'][0]} to {ho['test_range'][1]}) the models never saw.")

c = st.columns(5)
c[0].metric("P(show) AUC", f"{s['auc']:.2f}", help="0.5 = coin toss, 1.0 = perfect ranking")
c[1].metric("P(show) Brier", f"{s['brier']:.3f}", f"{s['brier'] - s['baseline_brier']:+.3f} vs naive",
            delta_color="inverse", help=f"Naive baseline (always predict {s['base_rate']:.0%}): {s['baseline_brier']:.3f}")
c[2].metric("P(effective) AUC", f"{e['auc']:.2f}", help="Among hearings where both sides appeared")
c[3].metric("P(effective) Brier", f"{e['brier']:.3f}", f"{e['brier'] - e['baseline_brier']:+.3f} vs naive",
            delta_color="inverse", help=f"Naive baseline: {e['baseline_brier']:.3f}")
c[4].metric("Duration MAE", f"{d['mae']:.1f} min", f"{d['mae'] - d['baseline_mae']:+.1f} vs reference table",
            delta_color="inverse", help=f"Mean actual hearing: {d['mean_actual']:.1f} min; MAPE {d['mape']:.0f}%")

st.subheader("Calibration: when the model says 70%, does it happen 70% of the time?")
l, r = st.columns(2)
l.plotly_chart(calibration_chart(ho["calibration"]["show"], f"P(show) · error {s['ece']:.1%}"),
               width="stretch")
r.plotly_chart(calibration_chart(ho["calibration"]["effective"], f"P(effective | heard) · error {e['ece']:.1%}"),
               width="stretch")

st.subheader("Back-test: predicted vs actual on real cause lists")
days = st.slider("Working days to back-test", 2, 15, 8)
bt = _backtest(days, 0)
bd, bs = bt["days"], bt["summary"]
c = st.columns(3)
c[0].metric("Heard per list: error", f"{bs['heard_mae']:.1f}", f"bias {bs['heard_bias']:+.1f}", delta_color="off")
c[1].metric("Effective per list: error", f"{bs['effective_mae']:.1f}", f"bias {bs['effective_bias']:+.1f}", delta_color="off")
c[2].metric("Court minutes: error", f"{bs['minutes_mae']:.0f} min", f"bias {bs['minutes_bias']:+.0f} min", delta_color="off")

label = bd.date.str[5:] + " " + bd.judge
fig = go.Figure()
fig.add_trace(go.Scatter(x=label, y=bd.pred_heard, name="Predicted heard", mode="lines+markers",
                         line=dict(color=BLUE, width=2), marker=dict(size=8)))
fig.add_trace(go.Scatter(x=label, y=bd.actual_heard, name="Actually heard", mode="lines+markers",
                         line=dict(color=ORANGE, width=2), marker=dict(size=8)))
fig.update_layout(height=340, margin=dict(l=10, r=10, t=20, b=10), hovermode="x unified",
                  yaxis=dict(title="Hearings", rangemode="tozero"), xaxis=dict(title="Day and court"),
                  legend=dict(orientation="h", y=1.1))
st.plotly_chart(fig, width="stretch")
st.dataframe(bd.round(1), hide_index=True, width="stretch")

st.subheader("Learning curve: accuracy grows with the court's own history")
lc = _learning(conn, n_train)
l, r = st.columns(2)
for col, metric, title in ((l, "auc", "P(show) AUC (higher is better)"), (r, "brier", "P(show) Brier (lower is better)")):
    f = go.Figure()
    if metric == "brier":
        f.add_trace(go.Scatter(x=lc.n_train, y=lc.baseline_brier, name="Naive baseline", mode="lines",
                               line=dict(color=MUTED, dash="dash", width=2)))
    f.add_trace(go.Scatter(x=lc.n_train, y=lc[metric], name="Model", mode="lines+markers",
                           line=dict(color=BLUE, width=2), marker=dict(size=9)))
    f.update_layout(title=title, height=300, margin=dict(l=10, r=10, t=50, b=10),
                    xaxis=dict(title="Training hearings", type="log"), showlegend=metric == "brier",
                    legend=dict(orientation="h", y=-0.3))
    col.plotly_chart(f, width="stretch")

st.caption(
    "**How to read this.** AUC is how well the model ranks hearings: 0.5 is a coin toss, 1.0 is perfect. "
    "Brier is the average squared miss of each probability; beating the naive baseline means the model "
    "adds information beyond the court's average rate. On the calibration charts, points on the dashed "
    "diagonal mean the stated odds come true as often as claimed. The back-test builds real cause lists "
    "and plays out each listed hearing; bias above zero means the list expected more than happened. "
    "The outcomes are synthetic until PUCAR's hearing-failure data is loaded; the same code then scores "
    "the models on the court's real history.")
