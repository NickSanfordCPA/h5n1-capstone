# Reporting checklists (draft)

Draft 1, 2026-11-18, against `manuscript.md` draft 1. Two checklists: EPIFORGE 2020 (primary)
and the machine-learning items of TRIPOD+AI. Each row gives where the manuscript reports the
item and what is still missing. JMIR requires the completed checklist as a Multimedia Appendix.

The EPIFORGE item wording below is paraphrased from the published guideline (Pollett et al,
PLoS Med 2021;18(10):e1003793; read 2026-11-18). The TRIPOD+AI rows are **written from memory
of the statement's topics, not from its official checklist**: before submission, replace them
with the official item numbers and wording (Collins et al, BMJ 2024;385:e078378).

## EPIFORGE 2020

| # | Item (paraphrased) | Where in the manuscript | Status |
|---|---|---|---|
| 1 | Identify the work as forecasting or prediction research in the title or abstract | Title; Abstract, Objective and Methods | Done |
| 2 | Define the purpose and the forecasting targets | Introduction, Objectives; Methods, "Outcome, Unit and Data Split" | Done |
| 3 | Document the methods fully: model detail, fitting, assumptions | Methods, "Baseline Model", "Track A", "Track B"; Multimedia Appendix 2 (protocol) | Done; feature list is in the protocol, not the main text |
| 4 | State whether forecasts were prospective, real-time or retrospective | Methods, "Study Design and Reporting" (retrospective); "Exploratory Status" | Done |
| 5 | Describe the sources of input data, with references and identifiers | Methods, "Data Sources"; references 1, 10, 11 | **Gap:** the APHIS URL is in (access date to add); NOAA, Census of Agriculture, population and APHIS wild-bird sources still have no reference with URL and access date; the Meta search configuration is unavailable (stated) |
| 6 | Provide the source data, or explain why not | Data Availability | Partly: wording to confirm against platform terms |
| 7 | Describe input data processing: missing values, outliers, transformations | Methods, "Post Processing", "Baseline Model" (suppressed values, wild-bird dating), "Track A" (no imputation); Multimedia Appendix 3 | Done; median fills for weather and layer inventory are in the protocol only |
| 8 | State and describe the model type and its assumptions, with references | Methods, "Baseline Model" (random forest, reference 13), "Track B" (Platt calibration) | Done |
| 9 | Make the model code available, or explain why not | Data Availability | **Gap:** repository link TBD; the branch is not yet public |
| 10 | Describe and justify the validation approach | Methods, "Outcome, Unit and Data Split" (temporal split), "Tuning" (forward-chaining folds) | Done |
| 11 | Describe and justify the accuracy metric | Methods, "Track A" (PR-AUC), "Track B" (O/E ratio), "Statistical Analysis" | Done (the PR-AUC justification is in "Track A") |
| 12 | Compare with a benchmark or comparator model, and justify it | Methods, "Baseline Model" (S0, S3); Results, Table 1 | Done |
| 13 | Describe and justify the forecast horizon | Methods, "Outcome, Unit and Data Split" (7, 14, 28 days) | Done (whole weeks, to match the unit) |
| 14 | Present and explain uncertainty | Methods, "Statistical Analysis" (week-block bootstrap); every Results table | Done |
| 15 | Summarize results in nontechnical terms, including uncertainty | Discussion, "Principal Findings"; Abstract, Conclusions | Done |
| 16 | If forecasts are published as a data object, use a versioned, identified release | Not applicable: no forecast data object is released | n/a |
| 17 | Describe weaknesses: data quality and methodological limits | Discussion, "Limitations" | Done |
| 18 | Discuss implications for public health action | Discussion, "Implications for Surveillance" | Done |
| 19 | Discuss generalizability across populations | Discussion, "Limitations" | Done (last paragraph of "Limitations") |

## TRIPOD+AI, machine-learning items (topics, from memory; verify wording)

| Topic | Where in the manuscript | Status |
|---|---|---|
| Data sources, dates and setting | Methods, "Data Sources", "Outcome, Unit and Data Split" | Done |
| Outcome definition and how it was assessed | Methods, "Outcome, Unit and Data Split"; "Data Sources" (confirmed diagnosis date only) | Done |
| Predictors: definition, timing, and that they precede the outcome | Methods, "Baseline Model" (wild-bird dating), "Track A" (windows end on the forecast Sunday) | Done |
| Sample size and number of events per split | Methods, "Outcome, Unit and Data Split"; Table 1 | Done (development and test counts at 14 days are in the text) |
| Missing data | Methods, "Baseline Model", "Track A" | Done (see EPIFORGE 7) |
| Model specification, including all model-building steps | Methods, "Baseline Model"; Multimedia Appendix 2 | Done |
| Hyperparameter tuning: method, search space, data used | Methods, "Tuning" | Done; the full grid and fold scores are in `search_state.csv`, to attach |
| Equal tuning effort across compared models | Methods, "Tuning" (one setting for every model) | Done |
| Randomness: seeds and their variation | Methods, "Tuning"; Results, Table 1 footnote and Track A | Done |
| Performance measures, including calibration | Results, Table 1 (PR-AUC, lift, Brier); Track B (O/E of non-sighted state-weeks) | Done as a table (Multimedia Appendix 5.1, 14 days); no calibration figure, and the calibrated risk used in Track B is not tabulated |
| Internal versus external validation; data leakage safeguards | Methods, "Outcome, Unit and Data Split"; "Exploratory Status" | Done; no external validation, stated |
| Subgroup or heterogeneity of performance | Results, Table 2 (quarters), Track B (new onset), Sensitivity Analyses | Done |
| Fairness | Not applicable in the usual sense (units are states, not people) | State as n/a |
| Use of large language models as part of the method | Methods, "Labelling by a Large Language Model"; Use of Generative AI | Partly: model version identifiers and dates of labelling are not in the text |
| Human oversight and validation of AI components | Methods and Results, "Human Validation" | **Pending** |
| Code and data availability | Data Availability | Gap (see EPIFORGE 9) |
| Protocol and registration | Methods, "Study Design and Reporting", "Exploratory Status" | Done: protocol committed, not registered, stated as exploratory |
| Patient and public involvement | None | State "none" |

## Gaps still open (updated 2026-11-18 after the self-review)

Closed since draft 1: reference 6 supplied and references verified (see `self_review.md`);
calibration table (Multimedia Appendix 5.1); development-period counts; PR-AUC and horizon
justifications; generalizability paragraph.

1. Data-source references with URLs, access dates and identifiers for NOAA, the Census of
   Agriculture, county population and the APHIS wild-bird file; the access date for reference 1.
2. LLM model version identifiers and labelling dates.
3. Public code link.
4. Human validation results.
5. The TRIPOD+AI rows above are still from memory; replace with the official items.
6. Compute cost of fitting and labelling is not reported anywhere.
