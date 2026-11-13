"""IPL team dashboard. Run with:  streamlit run app.py

Pick a team (main group), then a season (sub-unit). Every chart answers one
plain-language question. Numbers come from data_processing.py, so the CSV
download matches kpi_summary.csv when no filters are applied.
"""
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from data_processing import (MIN_BALLS_PHASE, MIN_DECIDED_RELIABLE, MIN_DISMISSALS,
                             PHASE_LABELS, phase_table, scope_filters, season_order,
                             season_win_table, team_kpis)

OUT_DIR = Path(__file__).resolve().parent / "output"
STATUS_OPTIONS = ["All matches", "Decided matches only", "No result only"]


@st.cache_data
def load_data():
    matches = pd.read_csv(OUT_DIR / "cleaned_matches.csv", dtype={"season": str},
                          parse_dates=["date"])
    balls = pd.read_csv(OUT_DIR / "cleaned_ball_by_ball.csv", dtype={"season": str},
                        low_memory=False)
    return matches, balls


def fmt(value, suffix="", decimals=1):
    """Format a number with thousands separators; show 'n/a' when there is no data."""
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value:,.{decimals}f}{suffix}"


st.set_page_config(page_title="IPL Team Dashboard", layout="wide")
st.title("IPL team performance, 2008 to 2026")

if not (OUT_DIR / "cleaned_matches.csv").exists():
    st.error("No cleaned data found. Run `python data_processing.py <ball.csv> <matches.csv>` first.")
    st.stop()

matches, balls = load_data()
teams = sorted(set(matches["team1"]) | set(matches["team2"]))
order = season_order(matches)

# ------------------------------------------------------------- sidebar filters
with st.sidebar:
    st.header("Filters")
    team = st.selectbox("Main group: team", teams,
                        index=teams.index("Mumbai Indians") if "Mumbai Indians" in teams else 0)
    team_seasons = [s for s in order
                    if ((matches["season"] == s) & ((matches["team1"] == team) | (matches["team2"] == team))).any()]
    season = st.selectbox("Sub-unit: season", ["All seasons"] + team_seasons)

    min_d, max_d = matches["date"].min().date(), matches["date"].max().date()
    date_range = st.date_input("Match date range", value=(min_d, max_d),
                               min_value=min_d, max_value=max_d)
    status = st.radio("Match status", STATUS_OPTIONS)

    with st.expander("More filters"):
        toss = st.selectbox("Toss decision", ["All", "bat", "field"])
        opponent = st.selectbox("Opponent", ["All teams"] + [t for t in teams if t != team])

if not isinstance(date_range, (tuple, list)) or len(date_range) != 2:
    st.info("Select an end date for the match date range.")
    st.stop()
start, end = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])

filters = dict(start=start, end=end, status=status, season=season, toss=toss)
m, b = scope_filters(matches, balls, team=team, opponent=opponent, **filters)
if m.empty:
    st.warning("No matches fit these filters. Widen the date range or change the status.")
    st.stop()

kpis = team_kpis(m, b)
if team not in kpis.index:
    st.warning(f"{team} has no matches with these filters.")
    st.stop()
row = kpis.loc[team]

# ------------------------------------------------------------- headline numbers
st.header(team)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Matches played", fmt(row.matches_played, decimals=0),
          help="Every match this team appeared in, including matches with no result.")
c2.metric("Win rate", fmt(row.win_rate_pct, "%"),
          help="Wins as a share of decided matches. No-result matches are left out.")
c3.metric("Runs per 100 balls", fmt(row.runs_per_100_balls),
          help="Runs scored per 100 legal deliveries faced, extras included.")
c4.metric("Death-overs runs per 100 balls", fmt(row.death_runs_per_100_balls),
          help="Overs 16 to 20 only.")

st.caption(f"Win rate uses {fmt(row.decided_matches, decimals=0)} decided matches out of "
           f"{fmt(row.matches_played, decimals=0)} played. Runs and dismissal rates use "
           f"{fmt(row.legal_balls_faced, decimals=0)} legal balls faced.")
if row.decided_matches < MIN_DECIDED_RELIABLE:
    st.warning(f"Only {int(row.decided_matches)} decided matches in this view. "
               f"Treat the rates with caution.")

st.download_button("Download this team's KPI summary (CSV)",
                   data=kpis.loc[[team]].reset_index().to_csv(index=False).encode("utf-8"),
                   file_name=f"kpi_summary_{team.replace(' ', '_')}.csv", mime="text/csv")

# ------------------------------------------------------------- tabs: one question each
t1, t2, t3, t4, t5 = st.tabs(["How often do they win?", "Does winning the toss help?",
                              "How fast do they score?", "How do they get out?",
                              "How do all teams compare?"])

with t1:
    st.subheader(f"How often does {team} win, season by season?")
    sw = season_win_table(m, team, order)
    if sw.empty:
        st.info("No seasons to show.")
    else:
        fig = px.bar(sw, x="season", y="win_rate_pct", text="win_rate_pct", color="enough_matches",
                     color_discrete_map={"Enough matches": "#2a6f97", "Too few matches": "#9e9e9e"},
                     labels={"season": "Season", "win_rate_pct": "Win rate (%)",
                             "enough_matches": "Sample size"})
        fig.update_traces(texttemplate="%{text:.0f}%", textposition="outside")
        fig.update_layout(yaxis_range=[0, 110], legend_title_text="")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Each bar is the share of decided matches won that season. Grey bars have fewer than 5 decided matches.")

with t2:
    st.subheader(f"Does winning the toss help {team}?")
    toss_df = pd.DataFrame({
        "Toss result": ["Won the toss", "Lost the toss"],
        "Win rate (%)": [row.win_rate_after_toss_win_pct, row.win_rate_after_toss_loss_pct],
        "Decided matches": [int(row.decided_after_toss_win), int(row.decided_after_toss_loss)],
    })
    fig = px.bar(toss_df, x="Toss result", y="Win rate (%)", text="Win rate (%)",
                 hover_data=["Decided matches"])
    fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside", marker_color="#2a6f97")
    fig.update_layout(yaxis_range=[0, 110])
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Share of decided matches won, split by whether the team won the toss.")
    if min(toss_df["Decided matches"]) < MIN_DECIDED_RELIABLE:
        st.warning("One of these groups has fewer than 20 decided matches, so the gap may be chance.")

with t3:
    st.subheader(f"How fast does {team} score in each phase of the innings?")
    pt = phase_table(b[b["batting_team"] == team])
    if pt.empty:
        st.info("No deliveries to show.")
    else:
        season_list = [s for s in order if s in set(pt["season"])]
        rate = (pt.pivot(index="season", columns="phase", values="runs_per_100_balls")
                .reindex(index=season_list, columns=PHASE_LABELS))
        legal = (pt.pivot(index="season", columns="phase", values="legal_balls")
                 .reindex(index=season_list, columns=PHASE_LABELS))
        masked = rate.where(legal >= MIN_BALLS_PHASE)
        text = masked.map(lambda v: "" if pd.isna(v) else f"{v:.0f}").to_numpy()
        fig = go.Figure(go.Heatmap(z=masked.to_numpy(), x=PHASE_LABELS, y=season_list,
                                   colorscale="Viridis", colorbar_title="Runs per 100 balls",
                                   text=text, texttemplate="%{text}",
                                   hovertemplate="%{y}, %{x}: %{z:.1f} runs per 100 balls<extra></extra>"))
        fig.update_yaxes(autorange="reversed", title="Season")
        fig.update_xaxes(title="Phase of the innings")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Runs per 100 legal balls by phase. Powerplay is overs 1 to 6, middle overs 7 to 15, death overs 16 to 20. "
                   f"Blank cells had fewer than {MIN_BALLS_PHASE} legal balls, so they are not shown.")

with t4:
    st.subheader(f"How are {team}'s batters dismissed?")
    d = b[(b["batting_team"] == team) & (b["counts_as_dismissal"] == 1)]
    if len(d) < MIN_DISMISSALS:
        st.warning(f"Only {len(d)} dismissals in this view, which is too few to read the mix reliably.")
    if d.empty:
        st.info("No dismissals to show.")
    else:
        dk = d["wicket_kind"].value_counts().rename_axis("How they got out").reset_index(name="Dismissals")
        dk["Share (%)"] = (100 * dk["Dismissals"] / dk["Dismissals"].sum()).round(1)
        fig = px.bar(dk.sort_values("Share (%)"), x="Share (%)", y="How they got out",
                     orientation="h", text="Share (%)", hover_data=["Dismissals"])
        fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside", marker_color="#2a6f97")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Share of this team's dismissals by method. Retirements and obstructing the field are excluded.")

with t5:
    st.subheader("How do all teams compare on win rate and scoring speed?")
    m_all, b_all = scope_filters(matches, balls, **filters)
    allk = team_kpis(m_all, b_all).reset_index()
    allk["Group"] = allk.apply(lambda r: "Selected team" if r["team"] == team else
                               ("Few decided matches" if r["decided_matches"] < MIN_DECIDED_RELIABLE
                                else "Other teams"), axis=1)
    cmap = {"Selected team": "#e76f51", "Few decided matches": "#bbbbbb", "Other teams": "#2a6f97"}
    left, right = st.columns(2)
    with left:
        fig = px.bar(allk.sort_values("win_rate_pct"), x="win_rate_pct", y="team", orientation="h",
                     color="Group", color_discrete_map=cmap,
                     labels={"win_rate_pct": "Win rate (%)", "team": ""})
        fig.update_layout(showlegend=False, height=520)
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Win rate across all teams, in this filter view.")
    with right:
        fig = px.bar(allk.sort_values("runs_per_100_balls"), x="runs_per_100_balls", y="team",
                     orientation="h", color="Group", color_discrete_map=cmap,
                     labels={"runs_per_100_balls": "Runs per 100 balls", "team": ""})
        fig.update_layout(showlegend=False, height=520)
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Scoring speed across all teams, in this filter view.")
    small = allk.loc[allk["decided_matches"] < MIN_DECIDED_RELIABLE, "team"].tolist()
    if small:
        st.info("Grey bars have fewer than 20 decided matches in this view: " + ", ".join(small) + ".")
