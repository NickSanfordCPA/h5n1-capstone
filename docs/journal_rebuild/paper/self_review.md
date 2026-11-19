# Review working draft — DRAFT-2026-H5N1

> Private working document. Human review and factual verification are required. This is a
> pre-submission self-review of `manuscript.md` (draft 1, as edited 2026-11-18), written with
> the `paper-review` skill by the same AI assistant that ran the analysis and drafted the
> paper. It is not an independent review and it does not make or imply an editorial decision.

## Review record

- Mode: `self-review (author pre-submission check; no editor channel)`
- Target venue: JMIR Public Health and Surveillance. Reporting guidance: EPIFORGE 2020, with
  the machine-learning items of TRIPOD+AI (`checklists.md`).
- Materials reviewed: `manuscript.md`, `appendices.md`, `checklists.md`, `PROTOCOL.md`,
  `PROGRESS.md`, `funnel.md`, the four figures, and the result files under
  `data\journal\models\` and `data\journal\supplement\`.
- Not available: human coding results, the Meta Content Library search configuration, an
  IRB determination, the full text of references 6, 15, 16 and 17.
- Each comment below is marked **Fixed**, **Partly fixed** or **Open** for this draft.

# Comments to authors

## Evidence-bounded summary

The manuscript asks whether public Facebook and X posts add to an environmental and
epidemiological baseline for forecasting confirmed H5N1 outbreaks in US poultry at the
state-week level, with 2022-2024 as development data and 2025-2026 as test data. It reports
two primary results. Track A: adding post volume and sentiment to a random-forest baseline
does not change PR-AUC on new-onset state-weeks (Facebook −0.000, 95% CI −0.013 to 0.011; X
−0.002, 95% CI −0.010 to 0.006). Track B: state-weeks with a post reporting sick or dead
animals at a named place had more outbreaks in the following 14 days than the calibrated
baseline expected (Facebook O/E ratio 1.21, 95% CI 1.06 to 1.37; X 1.13, 95% CI 0.97 to
1.31), with no evidence of a difference between platforms. Mechanism checks report that the
excess sits in relayed wildlife reports, does not fade when early days of the outcome window
are excluded, and is not removed by adding news volume or WHISPers counts to the baseline.
The paper describes itself as exploratory.

## Strengths

- The comparison is against a baseline that already contains the state's own recent
  outbreaks and wild-bird detections, which is the comparison earlier work often lacks
  (Methods, "Baseline Model"; Table 1).
- One forest setting for every model, five seeds, and a paired week-block bootstrap (Methods,
  "Tuning" and "Statistical Analysis"). Differences between models cannot be attributed to
  unequal tuning.
- The baseline's look-ahead is found and removed, and its cost is reported (Results,
  "Baseline": 0.022 PR-AUC at 14 days).
- Both platforms pass through one pipeline, and the difference between platforms is estimated
  directly instead of being read from overlapping intervals (Results, "Mechanism Checks").
- Negative and inconvenient results are in the text: the Track A null, the uninformative
  news-story comparison, the dependence of the X result on three accounts, and the weak
  new-onset baseline.
- The Track A null survives every sensitivity analysis (Multimedia Appendix 7), and the
  capstone supplement shows it is not sensitive to language or to emoji.

## Major comments

### Major comment M1

**The Track B finding re-estimates a pattern already seen in the test data.** Status: Open.

- Location: Abstract, Conclusions; Methods, "Exploratory Status"; Discussion, "Principal
  Findings" and "Limitations".
- Observation: Earlier analyses of the same 2025-2026 outcomes had found an O/E ratio near
  1.22 for Facebook sightings. The rebuilt analysis finds 1.21. The mechanism checks were
  designed after that pattern was seen. The appendix comparison shows the two Facebook
  versions share about three-quarters of their sighted state-weeks.
- Evidence or criterion: Prespecification and multiplicity. A protocol fixed before this
  analysis's models were fitted does not make the test period unseen.
- Why it matters: The 95% CI (1.06 to 1.37) understates uncertainty about whether the excess
  would recur. A reader could take a rebuilt, protocol-driven analysis as confirmation when
  it is largely the same evidence analysed again.
- Requested action: Keep every statement of the Track B result hedged as it now is, and do
  not strengthen it in revision. The real test is prospective: freeze the pipeline and guide
  now and score state-weeks after July 13, 2026 as they accrue. If the team can state that
  plan and its start date in the paper, do so. The development-period estimate (1.16, 95% CI
  0.97 to 1.35) is the nearest thing to a second look and is already in Table 3; consider
  naming it in the abstract if words allow.

### Major comment M2

**The labels have no human validation yet, and all come from one vendor's models.** Status: Open.

- Location: Methods, "Labelling by a Large Language Model" and "Human Validation"; Results,
  "Human Validation"; Abstract, Results.
- Observation: The sighting definition, the content classes and the author types rest on
  model labels. Agreement is reported only between two readings by the same model, with a
  third reading by a model from the same vendor. Human coding is in progress.
- Evidence or criterion: TRIPOD+AI (human oversight of AI components); the journal's
  expectations for AI-derived measurements.
- Why it matters: Two readings by one model share its systematic errors, so high κ between
  them shows consistency, not accuracy. Track B depends on a label (sick or dead animals at
  a named local place, not a poultry announcement) that has never been checked against a
  person.
- Requested action: Do not submit before the three coded workbooks are analysed
  (`kappa.py analyse`). Report weighted sensitivity and positive predictive value for the
  sighting label with intervals, and rerun Track B restricted to, or corrected for, the
  observed error if PPV is materially below 1. State the model version identifiers and the
  dates of labelling, which the draft omits.

### Major comment M3

**The O/E ratio compares state-weeks at very different levels of risk.** Status: Partly fixed.

- Location: Results, "Track B: Sightings", paragraph beginning "Sighted state-weeks had more
  than twice the expected risk"; Methods, "Statistical Analysis".
- Observation: Sighted state-weeks have a mean expected risk of 0.39 against 0.16 on
  Facebook. A baseline that under-predicts at high risk would give a ratio above 1 with no
  information in the posts. The draft now includes a post hoc comparison within fifths of
  expected risk (1.24 on Facebook, 1.14 on X) and a recalibrating logistic model (odds ratio
  1.59, 95% CI 1.20 to 2.11; 1.32, 95% CI 0.99 to 1.75). The same check showed that the rise
  of the ratio at a 10-day exclusion was partly miscalibration; the abstract now quotes the
  adjusted 10-day ratios (1.34 and 1.32), which have no interval.
- Evidence or criterion: Confounding by indication of the comparison group; analysis added
  after results were seen.
- Why it matters: This is the most direct alternative explanation of the headline result.
  Stratifying on five bins of the baseline's own prediction removes miscalibration in level
  but not confounding by anything the baseline omits that drives both wildlife die-off
  reporting and poultry risk (for example local wild-bird infection pressure that confirmed
  detections capture late). The absence of a dose-response (1.27, 1.24 and 1.10 for 1, 2 to 3
  and 4 or more posts) is read in the Discussion as "an event, not attention", but it is
  also what residual confounding would produce.
- Requested action: (a) Add bootstrap intervals for the risk-standardised ratios, including
  the 10-day ones now in the abstract. (b) In the Discussion, state the residual-confounding
  reading next to the "event" reading of the missing dose-response; done in this pass
  ("Interpretation", paragraph 2). (c) Keep the post hoc label.

### Major comment M4

**Every X result is provisional and structurally weaker than the Facebook result.** Status: Open.

- Location: Methods, "Data Sources" (X); Results, "Posts and Labels" and "Sensitivity
  Analyses"; Limitations.
- Observation: The English X series changes collector on the day the test period starts;
  three relay accounts wrote 62% of X sighting posts; removing the top 1% of authors leaves
  106 sighted state-weeks and a ratio of 1.10 (95% CI 0.88 to 1.35); and the X data are
  being re-collected.
- Evidence or criterion: Data provenance and measurement stability across the
  development-test boundary.
- Why it matters: "The platforms did not differ" is a statement about a Facebook estimate
  and an X estimate that mostly reflects a few accounts relaying official tables. One of
  those accounts posts the APHIS wild-bird table row by row, which is information the
  baseline already holds in another form.
- Requested action: Rerun the whole X arm on the re-pulled data before submission and
  report whether the collector break remains. Consider presenting X without the relay
  accounts as the main X estimate, or say plainly in the abstract that the X result depends
  on them. Until the rerun, no X number should be quoted outside the team.
- Update, 2026-11-19: partly resolved. The X arm was rerun on the re-pulled data. The
  collector break is gone: one collector and the same two query groups on both sides of
  January 1, 2025, with continuous volume and post mix across the boundary. Two points
  remain. (a) The re-pulled 2022-2024 period is less complete than 2025-2026 (no rows for
  5.4% of query-days against 0.5%; 66 busy days cut short against 4); the list is in
  `docs/journal_rebuild/x_repull_gaps.csv`. (b) X still depends on relay accounts: 3
  accounts wrote 48% of X sighting posts, and without the top 1% of authors the ratio is
  1.14 (95% CI 0.95 to 1.36) from 120 state-weeks. The account that posted the APHIS table
  row by row is almost absent from the new pull (25 sighting posts, from 1,279), so that
  particular objection no longer applies. The X test-period Track B and mechanism numbers
  did not change, because the 2025-2026 posts are the same posts; X Track A is now −0.010
  (95% CI −0.018 to 0.001).

## Minor comments

### Minor comment m1

**Abstract quoted the unadjusted 10-day ratios.** Status: Fixed.

- Location: Abstract, Results.
- Observation: The abstract gave 1.43 and 1.40 although the text explains that part of that
  rise is a calibration effect.
- Evidence or criterion: Abstract and text must agree on the estimate the authors stand
  behind.
- Why it matters: The abstract is what most readers see.
- Requested action: Done: the abstract now gives 1.34 and 1.32 "after adjustment for the
  level of expected risk" (444 words). See M3 for the missing intervals.

### Minor comment m2

**"Strong baseline" holds only for all state-weeks.** Status: Fixed.

- Location: Results, "Baseline"; Discussion, "Interpretation".
- Observation: On new-onset state-weeks the full baseline scores 0.254 against 0.246 for
  season and farm structure alone.
- Evidence or criterion: Table 1.
- Why it matters: Track A's primary outcome is on new-onset rows, where the comparator is
  weak; "against a stronger baseline" overstated it.
- Requested action: Done: the Discussion now says "a baseline that includes surveillance and
  environmental data", and the caution paragraph is kept.

### Minor comment m3

**Track A's null has no prespecified practical margin.** Status: Partly fixed.

- Location: Discussion, "Principal Findings".
- Observation: The draft said the intervals "rule out a gain of practical size" without
  defining one.
- Evidence or criterion: A claim of no effect needs a margin; a nonsignificant difference
  alone is not equivalence.
- Why it matters: Upper bounds of 0.011 and 0.006 on a PR-AUC near 0.36 are small, but
  whether they are negligible is a judgment the authors must own.
- Requested action: Reworded to state the bound and that no margin was set in advance. The
  team should decide whether to argue a margin (for example in outbreaks correctly flagged
  per 100 alerts) in the Discussion.

### Minor comment m4

**Calibration was not shown.** Status: Fixed as a table.

- Location: Results, "Baseline"; Multimedia Appendix 5.1.
- Observation: No calibration figure or table was in draft 1.
- Evidence or criterion: TRIPOD+AI performance measures.
- Why it matters: Track B is a calibration-based measure.
- Requested action: A tenths-of-risk table for the 14-day forest is now in Appendix 5.1. The
  Platt-calibrated risk that Track B actually uses is not tabulated; add it, since the files
  exist.

### Minor comment m5

**Compute cost is not reported.** Status: Open.

- Location: Methods, "Tuning".
- Observation: No hardware, run time or labelling volume is given.
- Evidence or criterion: Model-comparison reporting; the reader cannot judge the cost of
  reading 66,590 posts twice with a language model.
- Why it matters: The paper argues that full reading by a language model is feasible.
- Requested action: Add one or two sentences: hardware, approximate run times for the
  forests, and the number of model readings (posts, accounts, third readings). Run times were
  not measured cleanly in this work and would need a timed rerun.

### Minor comment m6

**References: citations confirmed, descriptions of four works unread.** Status: Partly fixed.

- Location: Introduction, paragraph 2; Discussion, "Interpretation"; References 6, 15, 16, 17.
- Observation: See "Literature grounding". Reference 6 had the wrong first author and year
  and is about Canada; it is corrected. The sentences describing references 6, 15, 16 and 17
  rest on search summaries, not on the papers.
- Evidence or criterion: A citation must support the sentence it is attached to.
- Why it matters: Reference 16 (Soltani et al) is the closest competing claim.
- Requested action: An author must read these four papers and correct the sentences.
  Check in particular what baseline, if any, Soltani et al compare against, since the
  Discussion's explanation of the disagreement assumes none that includes recent outbreaks.

### Minor comment m7

**Data-source references are incomplete.** Status: Open.

- Location: Methods, "Data Sources"; References.
- Observation: NOAA weather, the Census of Agriculture, county population and the APHIS
  wild-bird file have no reference; reference 1 lacks an access date.
- Evidence or criterion: EPIFORGE item 5.
- Why it matters: The baseline cannot be rebuilt from the paper.
- Requested action: Add them with URLs, identifiers and access dates from the ingest records.

### Minor comment m8

**The news-like cell is nearly the whole sample.** Status: Open, wording.

- Location: Results, "Mechanism Checks", "Kind of post".
- Observation: 285 of 290 Facebook and 413 of 418 X sighted state-weeks are news-like. The
  text says so, but the platform-difference model is then reported on the news-like effect.
- Evidence or criterion: Internal consistency.
- Why it matters: A reader may take the pooled model as a test of news against personal
  posts, which the data cannot support.
- Requested action: Say in one clause that the pooled model's news-like term is in effect
  the overall platform comparison.

### Minor comment m9

**Small count differences between sections.** Status: Open.

- Location: Results, Table 3 (28 days); `PROGRESS.md` timing description.
- Observation: X has 418 sighted test state-weeks at 14 days and 417 at 28 days (one loses
  its complete 28-day window), and the progress log's timing description counts 292 and 419
  where the manuscript has 290 and 418.
- Evidence or criterion: Denominators should be traceable.
- Why it matters: Harmless, but a reviewer who recomputes will ask.
- Requested action: Add a table footnote on the 28-day denominator; reconcile the 292 and
  419 in the log (they are not in the manuscript).

### Minor comment m10

**Figure 4B labels.** Status: Open.

- Location: Figure 4, panel B.
- Observation: Two state-week count labels touch.
- Evidence or criterion: Legibility at print size.
- Why it matters: Cosmetic.
- Requested action: Offset the labels in `figures.py` when the figures are regenerated for
  the X rerun.

### Minor comment m11

**A language result sits outside the paper.** Status: Open, decision for the team.

- Location: Limitations, last paragraph; `docs\journal_rebuild\supplement.md`.
- Observation: The capstone supplement finds that English-only sightings reproduce the
  Track B result, while the 40 Facebook state-weeks with a Spanish-language sighting show no
  excess (0.69, 95% CI 0.43 to 0.96). The paper says its results apply to "posts in English
  and Spanish".
- Evidence or criterion: Generalizability statements should match the evidence.
- Why it matters: The brief keeps the supplement out of the manuscript, but this one number
  bears on a sentence in it.
- Requested action: Decide whether to add one sentence (the excess came from
  English-language posts; Spanish-language sightings were too few to assess) or to leave the
  language analysis out entirely.

## Methods, statistics, and reproducibility

- **Design and unit.** State-week with a temporal split; the bootstrap resamples weeks, which
  matches the dependence structure across states. Serial dependence across adjacent weeks
  within a state (overlapping 14-day outcome windows) is not addressed by a one-week block;
  intervals may be somewhat narrow. A sensitivity run with longer blocks (for example 4
  weeks) would answer this and was not done.
- **Multiplicity.** Two primaries, many secondaries, no correction; stated. The first-hand,
  personal and Spanish cells should not be interpreted individually.
- **Selection into Track A.** About 30% of state-weeks have posts; the conclusion is limited
  to those, and the text says so.
- **Secondary offset model in Track A.** The positive volume coefficient (0.21 per SD on
  both platforms) uses an uncalibrated offset; reported with that caveat. It should not be
  dropped, since it is the one result pointing the other way.
- **Reproducibility.** One code base, one analyst, no independent recomputation; the
  sensitivity code reproduces both primaries through a separate path. Code is on a local
  branch and not public. Seeds, grid and fold scores are saved. Post text cannot be shared.
- **Departures.** The post hoc risk-level check and the counting of exclusion days from the
  forecast Sunday are logged in `PROGRESS.md`.

## Model-comparison checks

- **Equal tuning budget: met.** One setting, chosen for the baseline alone on development
  folds, is used for every model (Methods, "Tuning"); a fixed alternative forest is a
  sensitivity analysis. The searched setting was not better than the alternative on the test
  period, which the appendix reports for the county model; the same is true at state level
  for new onsets (0.254 against 0.266) and is not in the manuscript. Consider adding it.
- **Seed variance: met.** Five seeds, averaged; SD reported (0.003 or less for the baseline;
  0.005 to 0.009 on Track A rows).
- **Significance testing between models: met with limits.** Paired week-block bootstrap of
  the difference on identical test rows; no multiplicity correction; no prespecified
  practical margin (m3).
- **Compute-cost reporting: missing** (m5).

## Literature grounding

Searches were run on 2026-11-18 by a subagent using web search and page fetch (no Consensus
connector). Its report, not its query log, was retained; see the audit log below.

- **Reference existence (14 original entries).** Confirmed against publisher, Crossref or
  the ACL Anthology: references 1, 2, 3, 4, 8, 9, 12, 13, 14. Corrected: 6 (first author
  Movahedi Nia, 2026, J R Soc Interface; Canadian data) and 7 (authors' initials, date and
  DOI from the medRxiv page). Partly confirmed: 10 (venue and date; no first-hand copy), 11
  (DOI resolves; no official suggested citation found). Not checked: 5 (unpublished report).
- **Load-bearing background claims.** The draft-1 sentence that reference 6 did not compare
  against elementary baselines came from an unverified note and was removed. The statement
  that reference 7 shows good county-level discrimination was not re-read against the
  preprint in this session.
- **Works added.** Pang et al (J Med Internet Res 2025;27:e70746), Soltani et al (Expert
  Syst Appl 2025;291:128482) and Petie et al (PLoS One 2026;21(2):e0341829) were returned by
  the searches and are now references 15 to 17. Their descriptions come from search
  summaries (m6).
- **Leads not resolved.** McDuie et al, Transbound Emerg Dis 2024, article 5525298 (author
  list and DOI not retrieved); a 2024 spillover-risk framework attributed to Prosser et al;
  and a 2026 JMIR article (e73275) on Facebook and Instagram posts about HPAI. The last is
  likely to be directly relevant to this paper and should be read before submission.
- **Gap claim.** The searches returned no study testing wildlife mortality reports as a
  predictor of US poultry outbreaks and none combining Facebook or X posts with an
  environmental baseline. This is a handful of searches, not a systematic review, and zero
  results are incomplete coverage, not confirmation. The manuscript says "our search was not
  systematic".

## Ethics, transparency, figures, tables, and citations

- No IRB determination yet; the Ethical Considerations paragraph is a placeholder.
- Meta Content Library publication terms are unconfirmed, including whether results from the
  interface export may be published and whether post identifiers can be shared (Data
  Availability carries a "to confirm" tag).
- AI use: the journal asks for an AI-use declaration at submission and for prompts and
  transcripts to be kept. The labelling guides are an appendix; the team has not decided what
  else is submitted. The model version identifiers are missing from the text.
- Authorship, funding, conflicts and acknowledgments are placeholders.
- Post content is paraphrased and no account is named; the three relay accounts are
  described, not identified.
- Tables 1 to 3 were re-checked against the result files on 2026-11-18 and agree. Figures
  were inspected when made; they were not regenerated in this pass.
- Multimedia Appendices are now numbered in order of first citation (guides 4, baseline
  details 5, comparison 6, sensitivity 7).

## Limitations of this review

- The reviewer is the system that produced the analysis and the draft, so this review
  cannot find errors that the analysis and the reviewer share.
- No analysis was independently reproduced. The supplement and the county roll-up were each
  run once.
- Infectious-disease epidemiology and veterinary surveillance practice are outside what this
  review can judge with authority; the biological plausibility paragraph in the Discussion
  needs a domain reader.
- TRIPOD+AI coverage was assessed against a from-memory list of topics, not the official
  checklist.
- The full texts of references 6, 15, 16 and 17 were not read.

## Search audit log

| # | Query | Tool | Filters | Results received | Purpose |
|---|-------|------|---------|------------------|---------|
| 1-n | Not retained: the subagent returned findings without its query list | Web search and page fetch | None recorded | Not recorded | Reference existence for 14 entries; recent work on social media and avian influenza forecasting; wildlife mortality as a predictor |

The query-level log required by the review protocol does not exist for this pass. Works
cited in this review were drawn only from that subagent's search results on 2026-11-18;
nothing was added from model knowledge. Before submission the literature step should be
repeated with a logged search, at least for the gap claim and the 2026 JMIR lead.
