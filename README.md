# IPL Team Performance Dashboard

An analysis of every IPL match from 2008 to 2026 (1,212 matches, 288,226 deliveries). It answers five plain-language questions: how often each team wins, whether winning the toss helps, how fast teams score in each phase of the innings, how batters get out, and how all teams compare.

## Setup

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## How to run each part

**1. Clean the data and build the outputs** (run once, or again if the raw files change):

```bash
python data_processing.py path/to/ipl_ball_by_ball.csv path/to/ipl_matches.csv
```

This regenerates everything in `output/`. The optional `--out` flag changes the output folder.

**2. Open the dashboard:**

```bash
streamlit run app.py
```

Pick a team in the sidebar, then a season to drill into it. Use the expander for toss decision and opponent filters. The download button saves the team's KPI summary for the current filters.

**3. Read the analysis notebook:**

```bash
jupyter notebook notebooks/analysis.ipynb
```

The notebook runs from the files in `output/`. It walks through data quality, then the business questions, then conclusions.

## Project files

| File                       | Purpose                                                                                                    |
| -------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `data_processing.py`       | Cleaning pipeline and the metric functions shared by the dashboard and notebook                            |
| `app.py`                   | Streamlit dashboard built with Plotly                                                                      |
| `.streamlit/config.toml`   | Dark theme settings                                                                                        |
| `notebooks/analysis.ipynb` | Data quality, business questions, conclusions                                                              |
| `output/`                  | Generated: cleaned CSVs, data-quality report, outlier report, cleaning summary, KPI summary, static charts |
| `requirements.txt`         | Every package the project imports                                                                          |

## Data

- **Ball-by-ball file:** one row per delivery. Used for runs, wickets, phases and dismissals.
- **Matches file:** one row per match. Used for results, toss and team records.
- **Main group:** the team. **Sub-unit:** the season.
- **Time period:** 18 April 2008 to 1 May 2026. The 2026 season is still in progress in this data.

## Cleaning rules

1. Exact duplicate rows are removed. None were found.
2. Whitespace is trimmed. Null markers (`NA`, `N/A`, `none`, `null`, `-`, blank) become missing values.
3. Numbers and dates are parsed. Any value that fails to convert is reported in the data-quality report. None failed.
4. Business rules set invalid values to missing and impute nothing. For example, runs per ball must be between 0 and 7. None were out of range.
5. Outliers are flagged with the IQR rule on first and second innings totals and listed in `output/outlier_report.csv`. They are never deleted; 48 innings were flagged for review.

Renamed franchises are merged to their current name: Delhi Daredevils to Delhi Capitals, Kings XI Punjab to Punjab Kings, Royal Challengers Bangalore to Royal Challengers Bengaluru, and Rising Pune Supergiants to Rising Pune Supergiant. Deccan Chargers and Kochi Tuskers Kerala stay separate.

Three columns are added to the cleaned deliveries:

- `legal_ball`: 1 when the delivery has no wide or no-ball. Balls faced exclude extras.
- `counts_as_dismissal`: 1 for a wicket, excluding retirements and obstructing the field.
- `phase`: Powerplay (overs 1 to 6), Middle (overs 7 to 15), Death (overs 16 to 20).

## Metric definitions and assumptions

- **Win rate** is wins divided by decided matches. The 25 matches with no result are excluded from the denominator.
- **Runs per 100 balls** is total runs, extras included, per 100 legal balls faced.
- **Dismissals** exclude retirements and obstructing the field.
- **Super overs** (innings 3 and above, 175 deliveries) are included in totals. They are too few to change any rate meaningfully.
- **Reliability:** team-level win rates with fewer than 20 decided matches are flagged. Season-level bars with fewer than 5 decided matches are grey. Heatmap cells with fewer than 120 legal balls are left blank. Dismissal mixes with fewer than 30 dismissals are flagged.
- **Win rate does not adjust for opposition strength.** A team that played mostly strong opponents may have a lower rate than its quality warrants.

## Main findings

- **Win rates over the full period:** Gujarat Titans lead with 60.9% of decided matches, but they have only 69 decided matches because they entered in 2022. Chennai Super Kings (56.2%, 258 decided) and Mumbai Indians (54.5%, 281 decided) have the highest rates over a long record. Pune Warriors have the lowest (26.7%, 45 decided).
- **The toss matters little overall.** Toss winners won 51.6% of decided matches. Teams that won the toss and chose to field won 54.6% of those matches. Teams that won the toss and chose to bat won 45.8%.
- **Death overs are the fastest phase.** Across all data, teams scored 167 runs per 100 balls in overs 16 to 20, against 134 in the powerplay and 132 in the middle overs.
- **Scoring in the death overs has risen.** The rate was 157 in 2021, 187 in 2024 and 183 in 2025.
- **Most dismissals are catches.** Caught accounts for 63.3% of dismissals, bowled 16.8%, run out 8.2%, and lbw 6.1%.

## Notes

- The Streamlit dashboard and Plotly charts need the packages in `requirements.txt`. The cleaning script needs only pandas and matplotlib.
- `output/cleaned_ball_by_ball.csv` is about 75 MB. Regenerate it with the cleaning script rather than editing it by hand.
