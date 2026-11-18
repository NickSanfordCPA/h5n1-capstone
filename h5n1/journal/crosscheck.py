"""Cross-check of this rebuild's Meta pipeline against Max's shared tables (brief 6.10).

    uv run python -m h5n1.journal.crosscheck        # needs the Cloud SQL proxy on 5433

Reads meta_post_analysis and meta_post_funnel from the shared database (read-only sessions)
and compares them, post by post, with journal.post and journal.post_funnel. Max's tables are
never an input to the analysis; this only shows how much the pipeline change moved things.
No outcome data. Output: data/journal/crosscheck/*.csv and a printed summary.
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from h5n1.journal.kappa import kappa
from h5n1.journal.localdb import DATA_DIR, local_engine, source_engine

OUT = DATA_DIR / "crosscheck"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with source_engine().connect() as cn:
        ma = pd.read_sql(text("SELECT post_id, state AS max_state, geo_tier AS max_tier, "
                              "use_sentiment AS max_candidate FROM meta_post_analysis"), cn)
        mf = pd.read_sql(text("SELECT post_id, state AS max_funnel_state, event_state, disposition_step AS max_step, "
                              "disposition AS max_disposition, use_sightings, sighting_week FROM meta_post_funnel"),
                         cn, parse_dates=["sighting_week"])
    with local_engine().connect() as cn:
        jp = pd.read_sql(text("SELECT post_id, trim(state) AS geo_state, geo_class, disposition_step AS rule_step, "
                              "reader_set FROM journal.post WHERE platform = 'meta'"), cn)
        jf = pd.read_sql(text("SELECT post_id, state AS reader_state, step, sentiment_set, week "
                              "FROM journal.post_funnel WHERE platform = 'meta'"), cn, parse_dates=["week"])
    d = jp.merge(ma, on="post_id", how="outer", indicator="in")
    print("unique posts: ours", len(jp), "Max", len(ma), "common", int((d["in"] == "both").sum()))
    d = d[d["in"] == "both"].drop(columns="in").merge(mf, on="post_id", how="left").merge(jf, on="post_id", how="left")
    lines = []

    # 1. geocoded state, every unique post. Ours keeps a state only when exactly one is named.
    ours = d.geo_state.where(d.geo_state.notna() & (d.geo_state != ""))
    theirs = d.max_state.where(d.max_state.notna() & (d.max_state != ""))
    both = ours.notna() & theirs.notna()
    geo = {"posts": len(d), "both_have_a_state": int(both.sum()),
           "same_state": int((ours[both] == theirs[both]).sum()),
           "only_ours": int((ours.notna() & theirs.isna()).sum()),
           "only_max": int((ours.isna() & theirs.notna()).sum()),
           "neither": int((ours.isna() & theirs.isna()).sum())}
    geo["agreement_where_both"] = geo["same_state"] / geo["both_have_a_state"]
    pd.Series(geo).to_csv(OUT / "geocode.csv", header=False)
    lines.append(("geocoded state, all unique posts", geo))
    (d[both & (ours != theirs)].groupby([ours.rename("ours"), theirs.rename("max")]).size()
     .sort_values(ascending=False).head(40).rename("posts").reset_index().to_csv(OUT / "geocode_disagreements.csv", index=False))
    print(pd.crosstab(d.geo_class.fillna("none"), d.max_tier.fillna("none")).to_string())

    # 2. the candidate sets
    rs, cand = d.reader_set.fillna(False).astype(bool), d.max_candidate.fillna(False).astype(bool)
    sets = {"our_reader_set": int(rs.sum()), "max_candidates": int(cand.sum()), "in_both": int((rs & cand).sum()),
            "only_ours": int((rs & ~cand).sum()), "only_max": int((~rs & cand).sum())}
    pd.Series(sets).to_csv(OUT / "candidate_sets.csv", header=False)
    lines.append(("reader set against Max's 30,369 candidates", sets))
    only_max = d[~rs & cand]
    print("Max candidates we removed, by our rule step:\n", only_max.rule_step.value_counts().sort_index().to_string())

    # 3. sighting posts. Max: steps 15 (same state-week) and 16 (sighting). Ours: steps 0 and 16.
    max_s = d.max_step.isin([15, 16])
    our_s = d.step.isin([0, 16])
    judged = d.max_step.ge(8) & d.step.notna()        # read for sightings in both pipelines
    j = d[judged]
    sight = {"max_sighting_posts": int(max_s.sum()), "our_sighting_posts": int(our_s.sum()),
             "both": int((max_s & our_s).sum()),
             "posts_judged_in_both": int(judged.sum()),
             "judged_both_yes": int((max_s & our_s & judged).sum()),
             "judged_max_only": int((max_s & ~our_s & judged).sum()),
             "judged_ours_only": int((~max_s & our_s & judged).sum()),
             "kappa_on_judged": kappa(max_s[judged].map(str), our_s[judged].map(str))}
    pd.Series(sight).to_csv(OUT / "sightings.csv", header=False)
    lines.append(("sighting posts", sight))
    print("Max sighting posts by our step:\n", d[max_s].step.fillna(-1).astype(int).value_counts().sort_index().to_string())
    print("Our sighting posts by Max's disposition:\n",
          d[our_s].max_disposition.fillna("not in Max's funnel").value_counts().to_string())
    same_state = (j.reader_state == j.event_state.fillna(j.max_funnel_state))[max_s[judged] & our_s[judged]]
    print(f"state agrees on posts both call sightings: {same_state.mean():.3f} of {len(same_state)}")

    # 4. sighted state-weeks
    # Max labels a week by its Monday; this rebuild by the Sunday that ends it
    mw = d[d.max_step == 16].assign(week=lambda x: x.sighting_week + pd.Timedelta(days=6), st=lambda x: x.event_state.fillna(x.max_funnel_state))
    mine = set(zip(d[d.step == 0].reader_state, d[d.step == 0].week))
    his = set(zip(mw.st, mw.week))
    weeks = {"our_sighted_state_weeks": len(mine), "max_sighted_state_weeks": len(his), "in_both": len(mine & his)}
    pd.Series(weeks).to_csv(OUT / "state_weeks.csv", header=False)
    lines.append(("sighted state-weeks", weeks))
    for name, rec in lines:
        print(f"\n{name}")
        for k, v in rec.items():
            print(f"  {k}: {v:.3f}" if isinstance(v, float) else f"  {k}: {v:,}")


if __name__ == "__main__":
    main()
