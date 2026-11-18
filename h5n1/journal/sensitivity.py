"""Sensitivity checks for the two primary results (protocol section 8.6).

    uv run --extra modeling python -m h5n1.journal.sensitivity [--smoke] [--only NAME ...]

Each check reruns both primaries with one thing changed:
    Track A   S4 - S3 PR-AUC (all rows and new onset), and with the post windows ending 5 days early
    Track B   the O/E ratio (all sightings and new onset), and with the first 5 days excluded

Checks (the collector switch, the 3- and 5-day exclusions and the 5-post threshold are already
in tracks.py):
    primary            nothing changed; must reproduce tracks.py
    collection_date    wild-bird features dated by Collection Date (the earlier baseline)
    waree_forest       300 trees, depth 6, min leaf 2, sqrt features, for every model
    whispers           S3 + WHISPers avian-event counts as the baseline in both tracks
    no_poultry_words   posts whose text contains a poultry word are dropped
    no_top_authors     the top 1% of each platform's authors by 2022-2024 reader-set volume dropped
    profile_state      X only: posts placed by the profile state where one exists
    onset30            new onset defined on 30 days, k = 14
    k30_onset30        horizon 30 days and new onset on 30 days (the earlier definitions)

Outputs in data/journal/models/: sensitivity_a.csv, sensitivity_b.csv, sensitivity_counts.csv.
Each finished check is saved (sensitivity_part_<name>.pkl) and skipped on a rerun; delete the
part file to recompute it.
"""
from __future__ import annotations

import argparse
import re
import unicodedata

import numpy as np
import pandas as pd
from sqlalchemy import text

from h5n1.features.x_state import estimate_pseudocount
from h5n1.journal import models as M
from h5n1.journal import tracks as T
from h5n1.journal.localdb import local_engine

WHISP = [f"log_{p}whisp_{w}d" for p in ("", "nbr_") for w in (7, 21, 60)]
# A poultry word in English or Spanish. Deliberately broad ("Turkey" the country is caught):
# the check asks whether the result needs posts that could be about a flock at all.
POULTRY_RE = re.compile(
    r"\b(?:poultry|chickens?|hens?|roosters?|turkeys?|broilers?|pullets?|flocks?|egg[\s-]laying|layers|"
    r"aves de corral|pollos?|gallinas?|gallos?|pavos?|av[ií]colas?|parvadas?)\b", re.I)
TOP_SHARE = 0.01


def load_posts() -> pd.DataFrame:
    """Every reader-set post that is in the sentiment set or is a sighting candidate that
    reached step 15 or later, with what the post-level checks need."""
    q = text(
        "SELECT f.platform, f.post_id, f.author_id, f.state, f.created_at AS ts, f.week, f.step, "
        "f.sentiment_set, s.sentiment, p.text, trim(p.ul_state) AS ul_state "
        "FROM journal.post_funnel f JOIN journal.post p USING (platform, post_id) "
        "LEFT JOIN journal.post_sentiment s USING (platform, post_id) "
        "WHERE f.sentiment_set OR f.step IN (0, 15, 16)")
    with local_engine().connect() as cn:
        d = pd.read_sql(q, cn, parse_dates=["week"])
    d["ts"] = pd.to_datetime(d["ts"], utc=True)
    d["poultry_word"] = d.text.map(lambda s: bool(POULTRY_RE.search(unicodedata.normalize("NFKC", s or ""))))
    d["ul_state"] = d.ul_state.replace("", np.nan)
    return d.drop(columns="text")


def top_authors() -> set[tuple[str, str]]:
    """(platform, author_id) of the top 1% of each platform's authors by reader-set posts
    dated through 2024. No outcome data."""
    q = text("SELECT platform, author_id, count(*) n FROM journal.post "
             "WHERE reader_set AND day <= '2024-12-31' GROUP BY 1, 2")
    with local_engine().connect() as cn:
        a = pd.read_sql(q, cn)
    out = set()
    for plat, g in a.groupby("platform"):
        top = g.sort_values(["n", "author_id"], ascending=[False, True]).head(int(np.ceil(TOP_SHARE * len(g))))
        out |= {(plat, x) for x in top.author_id}
        print(f"top authors {plat}: {len(top)} of {len(g)} authors, at least {int(top.n.min())} posts each",
              flush=True)
    return out


def track_a(sw: pd.DataFrame, posts: pd.DataFrame, params: dict, cols: list[str], k: int,
            platforms) -> list[dict]:
    rows = []
    for plat in platforms:
        p = posts[(posts.platform == plat) & posts.sentiment_set]
        p = p[["post_id", "state", "ts", "sentiment"]].assign(account_type="", is_negative=False)
        assert p.sentiment.notna().all()
        m = estimate_pseudocount(p, T.FIT_END)
        for name, lag in (("primary", 0), ("embargo5", 5)):
            df = sw.merge(T.post_features(p, sw, m, lag), on=["state", "day"], how="left")
            df = df[df.n_posts_7d >= 1].reset_index(drop=True)
            train, test = M.dev_masks(df, k)
            o = df.loc[test, ["state", "day", "new_onset"]].assign(y=df.loc[test, f"y{k}"].to_numpy())
            o = o.reset_index(drop=True)
            preds = {}
            for arm, c in (("S3", cols), ("S4", cols + T.POSTS)):
                X = T.design(df, c, train)
                preds[arm] = M.fit_predict(X[train], df.loc[train, f"y{k}"], X[test], params).mean(axis=0)
            base = {"platform": plat, "variant": name, "k": k, "sentiment_posts": len(p), "pseudocount": m,
                    "train_rows": int(train.sum())}
            rows += T.lift_rows(o, preds, base)
    return rows


_RISK: dict = {}


def track_b(sw: pd.DataFrame, posts: pd.DataFrame, params: dict, cols: list[str], k: int, platforms,
            risk_key: tuple) -> list[dict]:
    """risk_key identifies (table, forest, columns, k): the expected risk does not depend on
    the posts, so checks that only change the posts reuse it."""
    grid = set(zip(sw.state, sw.day))
    rows = []
    for gap in (0, 5):
        if (risk_key, gap) not in _RISK:
            y = T.gap_target(sw, k, gap)
            e_test, _ = T.expected_risk(sw.assign(y=y), "y", k, params, cols)
            _RISK[(risk_key, gap)] = (y, e_test)
        y, e_test = _RISK[(risk_key, gap)]
        t = sw[["state", "day", "new_onset"]].assign(y=y, e=e_test)
        for plat in platforms:
            s = posts[(posts.platform == plat) & posts.step.isin([0, 15, 16])]
            sighted = {x for x in zip(s.state, s.week) if x in grid}
            t["sighted"] = [x in sighted for x in zip(t.state, t.day)]
            d = t[t.e.notna()].reset_index(drop=True)
            for sl, g in (("all", d), ("new_onset", d[d.new_onset.astype(bool)])):
                if g.sighted.any():
                    rows.append({"platform": plat, "k": k, "gap_days": gap, "slice": sl,
                                 "sighting_posts": len(s), "sighted_state_weeks": len(sighted),
                                 **T.oe_ratio(g.reset_index(drop=True))})
    return rows


def run(smoke: bool, only: list[str] | None) -> None:
    out = M.OUT / "smoke" if smoke else M.OUT
    out.mkdir(parents=True, exist_ok=True)
    if smoke:
        T.CFG.update(n_boot=50)
    searched = M.chosen_params("state")
    posts = load_posts()
    # Step-15 posts sit outside the contiguous states by their text state; they matter only
    # when the state is replaced (profile_state). Everywhere else the grid drops them.
    sw = {("detected", 28): M.load_state_week("detected", 28), ("coll", 28): M.load_state_week("coll", 28),
          ("detected", 30): M.load_state_week("detected", 30)}
    d30 = sw[("detected", 30)]
    d30["y30"] = d30["target_outbreak_next_30d"].astype(float)
    d30.loc[~d30["target_complete_30d"], "y30"] = np.nan
    with local_engine().connect() as cn:
        ex = pd.read_sql(text("SELECT * FROM journal.state_week_extra"), cn, parse_dates=["day"])
    sw_w = sw[("detected", 28)].merge(ex[["state", "day"] + WHISP], on=["state", "day"], validate="1:1")
    top = top_authors()
    is_top = pd.Series([x in top for x in zip(posts.platform, posts.author_id)], index=posts.index)
    profile = posts.assign(state=posts.ul_state.fillna(posts.state))
    both = T.PLATFORMS

    # name: (state-week table, posts, forest, baseline columns, k, platforms, risk key)
    checks = {
        "primary": (sw[("detected", 28)], posts, searched, M.S3, 14, both, ("detected", "searched", "S3", 14)),
        "collection_date": (sw[("coll", 28)], posts, searched, M.S3, 14, both, ("coll", "searched", "S3", 14)),
        "waree_forest": (sw[("detected", 28)], posts, M.WAREE, M.S3, 14, both, ("detected", "waree", "S3", 14)),
        "whispers": (sw_w, posts, searched, M.S3 + WHISP, 14, both, ("detected", "searched", "S3W", 14)),
        "no_poultry_words": (sw[("detected", 28)], posts[~posts.poultry_word], searched, M.S3, 14, both,
                             ("detected", "searched", "S3", 14)),
        "no_top_authors": (sw[("detected", 28)], posts[~is_top], searched, M.S3, 14, both,
                           ("detected", "searched", "S3", 14)),
        "profile_state": (sw[("detected", 28)], profile, searched, M.S3, 14, ("x",),
                          ("detected", "searched", "S3", 14)),
        "onset30": (d30, posts, searched, M.S3, 14, both, ("detected", "searched", "S3", 14)),
        "k30_onset30": (d30, posts, searched, M.S3, 30, both, ("detected", "searched", "S3", 30)),
    }
    counts = []
    for name, (_, p, *_rest) in checks.items():
        for plat in _rest[-2]:
            q = p[p.platform == plat]
            counts.append({"check": name, "platform": plat, "sentiment_posts": int(q.sentiment_set.sum()),
                           "sighting_posts": int(q.step.isin([0, 15, 16]).sum())})
    pd.DataFrame(counts).to_csv(out / "sensitivity_counts.csv", index=False)
    print(pd.DataFrame(counts).to_string(index=False), flush=True)
    print(f"profile_state: X posts with a profile state {int((posts.platform.eq('x') & posts.ul_state.notna()).sum())}"
          f", of which it differs from the text state "
          f"{int((posts.platform.eq('x') & posts.ul_state.notna() & (posts.ul_state != posts.state)).sum())}",
          flush=True)

    a_rows, b_rows = [], []
    for name, (table, p, params, cols, k, plats, key) in checks.items():
        if only and name not in only:
            continue
        part = out / f"sensitivity_part_{name}.pkl"
        if part.exists():
            got = pd.read_pickle(part)
            print(f"{name}: resumed", flush=True)
        else:
            got = {"a": [{"check": name, **r} for r in track_a(table, p, params, cols, k, plats)],
                   "b": [{"check": name, **r} for r in track_b(table, p, params, cols, k, plats, key)]}
            pd.to_pickle(got, part)
            print(f"{name}: done", flush=True)
        a_rows += got["a"]
        b_rows += got["b"]
    A, B = pd.DataFrame(a_rows), pd.DataFrame(b_rows)
    A.to_csv(out / "sensitivity_a.csv", index=False)
    B.to_csv(out / "sensitivity_b.csv", index=False)
    pd.set_option("display.width", 250, "display.max_columns", 40, "display.max_rows", 400)
    print(A[A.comparison == "S4 - S3"][["check", "platform", "variant", "k", "slice", "rows", "events",
                                        "pr_auc_ref", "pr_auc_new", "delta", "lo", "hi"]].round(4).to_string(index=False))
    print(B[["check", "platform", "k", "gap_days", "slice", "sighted_state_weeks", "sightings", "followed",
             "expected", "oe_ratio", "lo", "hi"]].round(3).to_string(index=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    run(a.smoke, a.only)
