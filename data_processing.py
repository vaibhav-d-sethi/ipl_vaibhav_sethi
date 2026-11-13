#!/usr/bin/env python3
"""Cleaning pipeline for the IPL ball-by-ball and match datasets.

Usage:
    python data_processing.py RAW_BALL_BY_BALL.csv RAW_MATCHES.csv [--out output]

Writes into the output folder:
    cleaned_ball_by_ball.csv   cleaned deliveries, with derived columns
    cleaned_matches.csv        cleaned matches
    data_quality_report.md     what was found and changed, per column
    cleaning_summary.json      the same counts in machine-readable form
    outlier_report.csv         innings totals flagged by the IQR rule (kept, not deleted)
    kpi_summary.csv            one row per team, all data, no filters
    charts/*.png               static charts

The dashboard (app.py) and the notebook import the metric functions below,
so the numbers on every surface come from the same code.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# ---------------------------------------------------------------- settings
NULL_MARKERS = {"", "na", "n/a", "none", "null", "nan", "-"}

# Franchise renames: the same team under its earlier name. Merged so that a
# team's history is not split in two. Deccan Chargers and Kochi Tuskers Kerala
# are kept separate, as they were separate franchises.
TEAM_RENAMES = {
    "Delhi Daredevils": "Delhi Capitals",
    "Kings XI Punjab": "Punjab Kings",
    "Royal Challengers Bangalore": "Royal Challengers Bengaluru",
    "Rising Pune Supergiants": "Rising Pune Supergiant",
}

NON_DISMISSAL_KINDS = {"retired hurt", "retired out", "obstructing the field"}
PHASE_LABELS = ["Powerplay", "Middle", "Death"]

MIN_DECIDED_RELIABLE = 20   # team-level: fewer decided matches gets a warning
MIN_DECIDED_SEASON = 5      # season-level win rate reliability
MIN_BALLS_PHASE = 120       # heatmap cells with fewer legal balls are left blank
MIN_DISMISSALS = 30         # dismissal-mix charts need at least this many

MATCH_NUMERIC = ["match_id", "overs", "balls_per_over", "team1_won", "toss_winner_won"]
MATCH_DATES = ["date"]
MATCH_RULES = {"overs": (1, 50), "balls_per_over": (1, 12),
               "team1_won": (0, 1), "toss_winner_won": (0, 1)}
MATCH_TEAM_COLS = ["team1", "team2", "toss_winner", "winner"]

BALL_NUMERIC = ["match_id", "innings", "over", "ball_in_over", "ball_number", "batter_runs",
                "extra_runs", "total_runs", "wides", "noballs", "byes", "legbyes", "penalty",
                "is_wicket", "batting_team_won", "is_powerplay", "is_middle_overs", "is_death_overs"]
BALL_DATES = ["date"]
BALL_RULES = {"innings": (1, 6), "over": (0, 19), "ball_in_over": (1, 12), "ball_number": (1, 200),
              "batter_runs": (0, 6), "extra_runs": (0, 7), "total_runs": (0, 7),
              "wides": (0, 7), "noballs": (0, 7), "byes": (0, 6), "legbyes": (0, 6),
              "penalty": (0, 5), "is_wicket": (0, 1), "batting_team_won": (0, 1),
              "is_powerplay": (0, 1), "is_middle_overs": (0, 1), "is_death_overs": (0, 1)}
BALL_TEAM_COLS = ["batting_team", "bowling_team", "team1", "team2", "toss_winner", "match_winner"]


# ---------------------------------------------------------------- cleaning helpers
def _to_number(raw: pd.Series, col: str, failures: dict) -> pd.Series:
    num = pd.to_numeric(raw, errors="coerce")
    failed = raw.notna() & num.isna()
    failures[col] = {"count": int(failed.sum()), "examples": raw[failed].unique()[:5].tolist()}
    if num.dropna().mod(1).eq(0).all():
        num = num.astype("Int64")
    return num


def _to_date(raw: pd.Series, col: str, failures: dict) -> pd.Series:
    parsed = pd.to_datetime(raw, format="%Y-%m-%d", errors="coerce")
    failed = raw.notna() & parsed.isna()
    failures[col] = {"count": int(failed.sum()), "examples": raw[failed].unique()[:5].tolist()}
    return parsed


def clean_table(raw: pd.DataFrame, name: str, numeric, dates, rules, report: dict) -> pd.DataFrame:
    """Dedupe, trim, convert null markers, parse types, then apply business ranges."""
    info = {"raw_rows": int(len(raw))}
    df = raw.drop_duplicates().copy()
    info["duplicates_removed"] = int(len(raw) - len(df))

    nulls, trimmed = {}, 0
    for col in df.columns:
        stripped = df[col].str.strip()
        trimmed += int((stripped != df[col]).sum())
        is_null = stripped.str.lower().isin(NULL_MARKERS)
        nulls[col] = int(is_null.sum())
        df[col] = stripped.mask(is_null)
    info["whitespace_trimmed"] = trimmed
    info["null_markers_converted"] = nulls

    failures = {}
    for col in numeric:
        if col in df:
            df[col] = _to_number(df[col], col, failures)
    for col in dates:
        if col in df:
            df[col] = _to_date(df[col], col, failures)
    info["conversion_failures"] = failures

    invalid = {}
    for col, (lo, hi) in rules.items():
        if col not in df:
            continue
        s = df[col]
        in_range = ((s >= lo) & (s <= hi)).fillna(False).astype(bool)
        bad = s.notna() & ~in_range
        invalid[col] = {"count": int(bad.sum()), "allowed": [lo, hi]}
        df.loc[bad, col] = pd.NA
    info["invalid_values_set_missing"] = invalid
    info["clean_rows"] = int(len(df))
    info["missing_after_cleaning"] = {c: int(v) for c, v in df.isna().sum().items() if v}
    report[name] = info
    return df


def rename_teams(df: pd.DataFrame, cols, name: str, report: dict) -> pd.DataFrame:
    changes = {}
    for col in cols:
        if col in df:
            old = df[col]
            new = old.replace(TEAM_RENAMES)
            changes[col] = int((old.notna() & (old != new)).sum())
            df[col] = new
    report.setdefault("team_name_changes", {})[name] = changes
    return df


def add_ball_fields(b: pd.DataFrame) -> pd.DataFrame:
    def flag(s):
        return s.eq(1).fillna(False).astype(bool).to_numpy()

    wides = b["wides"].fillna(0)
    noballs = b["noballs"].fillna(0)
    b["legal_ball"] = ((wides == 0) & (noballs == 0)).astype(int)
    b["counts_as_dismissal"] = (flag(b["is_wicket"]) & ~b["wicket_kind"].isin(NON_DISMISSAL_KINDS)).astype(int)
    phase = np.select(
        [flag(b["is_powerplay"]), flag(b["is_middle_overs"]), flag(b["is_death_overs"])],
        PHASE_LABELS, default="")
    b["phase"] = pd.Series(phase, index=b.index).replace("", np.nan)
    return b


# ---------------------------------------------------------------- metric functions (shared)
def _pct(n, d):
    return (100 * n / d).where(d > 0).round(2)


def season_order(matches: pd.DataFrame) -> list:
    return matches.groupby("season")["date"].min().sort_values().index.tolist()


def scope_filters(matches, balls, *, start=None, end=None, status="All matches",
                  season="All seasons", toss="All", team=None, opponent=None):
    """Apply the dashboard filters. With no arguments, returns all data."""
    m = matches
    if start is not None:
        m = m[m["date"] >= start]
    if end is not None:
        m = m[m["date"] <= end]
    if status == "Decided matches only":
        m = m[m["winner"].notna()]
    elif status == "No result only":
        m = m[m["winner"].isna()]
    if season and season != "All seasons":
        m = m[m["season"] == season]
    if toss and toss != "All":
        m = m[m["toss_decision"] == toss]
    if team:
        m = m[(m["team1"] == team) | (m["team2"] == team)]
    if opponent and opponent != "All teams":
        m = m[((m["team1"] == team) & (m["team2"] == opponent)) |
              ((m["team2"] == team) & (m["team1"] == opponent))]
    b = balls[balls["match_id"].isin(m["match_id"])]
    return m, b


def phase_table(balls: pd.DataFrame) -> pd.DataFrame:
    """Runs and legal balls by batting team, season and phase."""
    d = balls[balls["phase"].notna()]
    key = ["batting_team", "season", "phase"]
    runs = d.groupby(key)["total_runs"].sum()
    legal = d[d["legal_ball"] == 1].groupby(key).size()
    out = pd.concat([runs.rename("runs"), legal.rename("legal_balls")], axis=1).fillna(0).reset_index()
    out["runs_per_100_balls"] = (100 * out["runs"] / out["legal_balls"]).where(out["legal_balls"] > 0).round(2)
    return out


def season_win_table(matches: pd.DataFrame, team: str, order: list) -> pd.DataFrame:
    t = matches[(matches["team1"] == team) | (matches["team2"] == team)].copy()
    t["won"] = (t["winner"] == team).astype(int)
    t["decided"] = t["winner"].notna().astype(int)
    g = t.groupby("season").agg(matches=("match_id", "size"), decided=("decided", "sum"), wins=("won", "sum"))
    g["win_rate_pct"] = _pct(g["wins"], g["decided"])
    g["enough_matches"] = np.where(g["decided"] >= MIN_DECIDED_SEASON, "Enough matches", "Too few matches")
    g = g.reindex([s for s in order if s in g.index]).reset_index()
    return g


def team_kpis(matches: pd.DataFrame, balls: pd.DataFrame) -> pd.DataFrame:
    """One row per team. Used by the KPI summary, the dashboard and the notebook,
    so every surface gives the same numbers for the same rows."""
    long = pd.concat([
        pd.DataFrame({"team": matches["team1"].values,
                      "decided": matches["winner"].notna().values,
                      "won": (matches["winner"] == matches["team1"]).values,
                      "won_toss": (matches["toss_winner"] == matches["team1"]).values}),
        pd.DataFrame({"team": matches["team2"].values,
                      "decided": matches["winner"].notna().values,
                      "won": (matches["winner"] == matches["team2"]).values,
                      "won_toss": (matches["toss_winner"] == matches["team2"]).values}),
    ], ignore_index=True)
    g = long.groupby("team")
    out = pd.DataFrame({"matches_played": g.size(),
                        "decided_matches": g["decided"].sum().astype(int),
                        "wins": g["won"].sum().astype(int)})
    dec = long[long["decided"]]
    tw = dec[dec["won_toss"]].groupby("team")["won"]
    tl = dec[~dec["won_toss"]].groupby("team")["won"]
    out["win_rate_pct"] = _pct(out["wins"], out["decided_matches"])
    out["decided_after_toss_win"] = tw.size().reindex(out.index).fillna(0).astype(int)
    out["win_rate_after_toss_win_pct"] = _pct(tw.sum().reindex(out.index).fillna(0),
                                              out["decided_after_toss_win"])
    out["decided_after_toss_loss"] = tl.size().reindex(out.index).fillna(0).astype(int)
    out["win_rate_after_toss_loss_pct"] = _pct(tl.sum().reindex(out.index).fillna(0),
                                               out["decided_after_toss_loss"])

    b = balls.assign(boundary=balls["batter_runs"].isin([4, 6]).astype(int))
    bg = b.groupby("batting_team").agg(runs_scored=("total_runs", "sum"),
                                       dismissals=("counts_as_dismissal", "sum"),
                                       boundaries=("boundary", "sum"))
    bg["legal_balls_faced"] = b.groupby("batting_team")["legal_ball"].sum()
    bg = bg.reindex(out.index).fillna(0)
    out = out.join(bg)
    out["runs_per_100_balls"] = _pct(out["runs_scored"], out["legal_balls_faced"])
    out["boundaries_per_100_balls"] = _pct(out["boundaries"], out["legal_balls_faced"])
    out["wickets_lost_per_100_balls"] = _pct(out["dismissals"], out["legal_balls_faced"])

    pt = phase_table(balls)
    death = pt[pt["phase"] == "Death"].groupby("batting_team")[["runs", "legal_balls"]].sum()
    death = death.reindex(out.index).fillna(0)
    out["death_runs_per_100_balls"] = _pct(death["runs"], death["legal_balls"])

    cols = ["matches_played", "decided_matches", "wins", "win_rate_pct",
            "decided_after_toss_win", "win_rate_after_toss_win_pct",
            "decided_after_toss_loss", "win_rate_after_toss_loss_pct",
            "runs_scored", "legal_balls_faced", "runs_per_100_balls",
            "boundaries_per_100_balls", "wickets_lost_per_100_balls", "death_runs_per_100_balls"]
    out = out[cols].round(2)
    out.index.name = "team"
    return out


def innings_outliers(balls: pd.DataFrame):
    """IQR rule on first and second innings totals. Flags only; nothing is deleted."""
    main = balls[balls["innings"].isin([1, 2])]
    t = (main.groupby(["match_id", "innings", "batting_team"])["total_runs"].sum()
         .reset_index(name="value"))
    q1, q3 = t["value"].quantile([0.25, 0.75])
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    t["lower_bound"], t["upper_bound"] = lo, hi
    flagged = t[(t["value"] < lo) | (t["value"] > hi)].copy()
    flagged.insert(0, "metric", "innings_total_runs")
    return flagged, {"metric": "innings_total_runs", "q1": float(q1), "q3": float(q3),
                     "lower_bound": float(lo), "upper_bound": float(hi),
                     "flagged_rows": int(len(flagged)), "innings_checked": int(len(t))}


# ---------------------------------------------------------------- charts
def make_charts(kpis: pd.DataFrame, matches: pd.DataFrame, balls: pd.DataFrame, out: Path):
    plt.rcParams.update({"figure.dpi": 110, "axes.spines.top": False, "axes.spines.right": False})

    k = kpis.sort_values("win_rate_pct")
    colors = ["#2a6f97" if d >= MIN_DECIDED_RELIABLE else "#bbbbbb" for d in k["decided_matches"]]
    fig, ax = plt.subplots(figsize=(9, 7))
    ax.barh(k.index, k["win_rate_pct"], color=colors)
    ax.set_xlabel("Win rate (% of decided matches)")
    ax.set_title("Which team wins the largest share of its decided matches?\n"
                 "Grey = fewer than 20 decided matches")
    fig.tight_layout(); fig.savefig(out / "charts" / "win_rate_by_team.png"); plt.close(fig)

    k = kpis.sort_values("runs_per_100_balls")
    fig, ax = plt.subplots(figsize=(9, 7))
    ax.barh(k.index, k["runs_per_100_balls"], color="#2a6f97")
    ax.set_xlabel("Runs per 100 legal balls faced")
    ax.set_title("Which team scores fastest?")
    fig.tight_layout(); fig.savefig(out / "charts" / "runs_per_100_balls_by_team.png"); plt.close(fig)

    order = season_order(matches)
    counts = matches.groupby("season").size().reindex(order)
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.bar(counts.index, counts.values, color="#2a6f97")
    ax.set_ylabel("Matches")
    ax.set_title("How many matches were played each season?")
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout(); fig.savefig(out / "charts" / "matches_per_season.png"); plt.close(fig)

    d = balls[balls["counts_as_dismissal"] == 1]["wicket_kind"].value_counts(normalize=True) * 100
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.barh(d.index[::-1], d.values[::-1], color="#2a6f97")
    ax.set_xlabel("Share of dismissals (%)")
    ax.set_title("How are batters dismissed? (retirements excluded)")
    fig.tight_layout(); fig.savefig(out / "charts" / "dismissal_types.png"); plt.close(fig)


# ---------------------------------------------------------------- pipeline
def run(ball_path: Path, matches_path: Path, out: Path):
    (out / "charts").mkdir(parents=True, exist_ok=True)
    report: dict = {}

    raw_m = pd.read_csv(matches_path, dtype=str, keep_default_na=False)
    raw_b = pd.read_csv(ball_path, dtype=str, keep_default_na=False, low_memory=False)

    tables: dict = {}
    m = clean_table(raw_m, "matches", MATCH_NUMERIC, MATCH_DATES, MATCH_RULES, tables)
    b = clean_table(raw_b, "ball_by_ball", BALL_NUMERIC, BALL_DATES, BALL_RULES, tables)
    report["tables"] = tables
    m = rename_teams(m, MATCH_TEAM_COLS, "matches", report)
    b = rename_teams(b, BALL_TEAM_COLS, "ball_by_ball", report)
    b = add_ball_fields(b)

    m_ids, b_ids = set(m["match_id"].dropna()), set(b["match_id"].dropna())
    report["unmatched_ids"] = {"in_balls_not_matches": len(b_ids - m_ids),
                               "in_matches_not_balls": len(m_ids - b_ids)}
    report["super_over_deliveries"] = int((b["innings"] >= 3).sum())

    m.to_csv(out / "cleaned_matches.csv", index=False, date_format="%Y-%m-%d")
    b.to_csv(out / "cleaned_ball_by_ball.csv", index=False, date_format="%Y-%m-%d")

    kpis = team_kpis(m, b)
    kpis.reset_index().to_csv(out / "kpi_summary.csv", index=False)

    flagged, outlier_info = innings_outliers(b)
    flagged.to_csv(out / "outlier_report.csv", index=False)
    report["outliers"] = outlier_info

    make_charts(kpis, m, b, out)

    with open(out / "cleaning_summary.json", "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    write_quality_report(report, m, b, out / "data_quality_report.md")
    print(f"Matches: {len(m):,} rows | Deliveries: {len(b):,} rows | Teams: {kpis.shape[0]}")
    print(f"Outputs written to {out.resolve()}")


def write_quality_report(report: dict, m: pd.DataFrame, b: pd.DataFrame, path: Path):
    lines = ["# Data quality report", "",
             "Generated by `data_processing.py`. Counts describe what was found and changed.", ""]
    for name, info in report["tables"].items():
        lines += [f"## {name}", "",
                  f"- Raw rows: {info['raw_rows']:,}",
                  f"- Exact duplicate rows removed: {info['duplicates_removed']:,}",
                  f"- Cells with stray whitespace trimmed: {info['whitespace_trimmed']:,}",
                  f"- Clean rows: {info['clean_rows']:,}", "",
                  "### Null markers converted to missing (NA, none, -, blank)", ""]
        nz = {k: v for k, v in info["null_markers_converted"].items() if v}
        lines += ([f"- `{k}`: {v:,}" for k, v in nz.items()] or ["- None found."]) + [""]
        lines += ["### Values that failed to convert", ""]
        bad = {k: v for k, v in info["conversion_failures"].items() if v["count"]}
        lines += ([f"- `{k}`: {v['count']:,} failed, e.g. {v['examples']}" for k, v in bad.items()]
                  or ["- None. Every number and date parsed."]) + [""]
        lines += ["### Business-rule violations set to missing (no imputation)", ""]
        badr = {k: v for k, v in info["invalid_values_set_missing"].items() if v["count"]}
        lines += ([f"- `{k}` outside {v['allowed']}: {v['count']:,}" for k, v in badr.items()]
                  or ["- None. All values are within their valid ranges."]) + [""]
        lines += ["### Columns still missing after cleaning", ""]
        miss = info["missing_after_cleaning"]
        lines += ([f"- `{k}`: {v:,}" for k, v in miss.items()] or ["- None."]) + [""]

    lines += ["## Team names", "",
              "Renamed franchises were merged to their current name:", ""]
    lines += [f"- `{old}` to `{new}`" for old, new in TEAM_RENAMES.items()]
    lines += ["", "Cells changed, per table: " + json.dumps(report["team_name_changes"]), ""]

    lines += ["## Derived columns added", "",
              "- `legal_ball`: 1 when the delivery has no wide or no-ball, otherwise 0. Balls faced exclude extras.",
              "- `counts_as_dismissal`: 1 for a wicket except retirements and obstructing the field.",
              "- `phase`: Powerplay (overs 1 to 6), Middle (overs 7 to 15), Death (overs 16 to 20), from the source flags.", ""]

    lines += ["## Linking and coverage", "",
              f"- Match IDs in ball-by-ball but not in matches: {report['unmatched_ids']['in_balls_not_matches']:,}",
              f"- Match IDs in matches but not in ball-by-ball: {report['unmatched_ids']['in_matches_not_balls']:,}",
              f"- Date range: {m['date'].min().date()} to {m['date'].max().date()}",
              f"- Matches with no result (abandoned or tied with no winner): {int(m['winner'].isna().sum()):,}",
              f"- Super-over deliveries (innings 3 and above): {report['super_over_deliveries']:,}. "
              "Kept in the totals; they are too few to change any rate meaningfully.", ""]

    o = report["outliers"]
    lines += ["## Outliers", "",
              f"IQR rule on first and second innings totals. Bounds: {o['lower_bound']:.1f} to {o['upper_bound']:.1f} runs.",
              f"{o['flagged_rows']:,} of {o['innings_checked']:,} innings were flagged for review in `outlier_report.csv`. "
              "They are kept in every metric; flagging is for review only.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Clean the IPL datasets and build the outputs.")
    ap.add_argument("ball_csv", type=Path, help="Path to ipl_ball_by_ball.csv")
    ap.add_argument("matches_csv", type=Path, help="Path to ipl_matches.csv")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "output")
    args = ap.parse_args()
    run(args.ball_csv, args.matches_csv, args.out)


if __name__ == "__main__":
    main()
