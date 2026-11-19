"""Human validation of the reader labels (protocol section 11; gate 2).

    uv run --extra journal python -m h5n1.journal.kappa sample     # design and draw, once
    uv run --extra journal python -m h5n1.journal.kappa packets    # one workbook per coder
    uv run --extra journal python -m h5n1.journal.kappa retained   # the sample against today's reader set
    uv run --extra journal python -m h5n1.journal.kappa analyse [--dir DIR]

sample. 400 posts from the labelled reader set, stratified by platform x language x stratum.
A post's stratum is the first that applies:
    sighting         a sighting post (funnel step 0, 15 or 16)
    disagreement     the two readers differed on an analysis field (the row is adjudicated)
    not_included     readers judged it not relevant or not about one US state (steps 9, 10)
    domestic_detection / wildlife_report / first_hand / other     its content class
The allocation is fixed in ALLOCATION and CELL_SHARE before drawing; a cell smaller than its
allocation gives its surplus to the other cells of the stratum. Each post's weight is its
cell's population over its cell's sample, so agreement can be reweighted to the reader set.
The design goes to docs/journal_rebuild/coding/sample_design.csv (no post text); the key that
links items to posts and reader labels stays in data/journal/coding/ (not in git, and not in
the folder the coders open). Refuses to redraw.

packets. One .xlsx per coder: a "Posts" sheet (date, language, the same first 1,500
characters the readers saw; no author, no reader label, no outcome) and an "Authors" sheet
(each sampled post's author once, by name, handle and bio or Page type; no posts). Item order
is shuffled separately for each coder.

retained. After the X re-pull of 2026-11-19 some sampled X posts are no longer in the reader
set. This step counts, cell by cell, what is left, and computes post-stratification weights
against the reader set as it is now. Nothing is redrawn and the workbooks do not change.

analyse. Krippendorff's alpha across the three coders and pairwise Cohen's kappa per field, on
every coded item. Then the majority of three against the reader labels (weighted kappa;
sensitivity and positive predictive value for sightings; bootstrap intervals) and the
geocoder's state accuracy, in four scopes: the primary one is the posts still in the reader
set with their current labels, weighted to the current reader set; the others are unweighted
checks on every coded post with the label it had at the draw, split by whether it remains.
"""
from __future__ import annotations

import argparse
import itertools
import re
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

from h5n1.journal.localdb import DATA_DIR, local_engine

ROOT = Path(__file__).resolve().parents[2]
CODING = ROOT / "docs" / "journal_rebuild" / "coding"
PRIVATE = DATA_DIR / "coding"
CODERS = ("Nick", "Max", "Waree")
SEED = 20261118
MAX_CHARS = 1500
N_BOOT = 2000
ALLOCATION = {"sighting": 100, "disagreement": 60, "not_included": 40, "domestic_detection": 50,
              "wildlife_report": 50, "first_hand": 50, "other": 50}
CELL_SHARE = {("meta", "en"): 0.35, ("x", "en"): 0.35, ("meta", "es"): 0.15, ("x", "es"): 0.15}
CLASSES = ["domestic_detection", "wildlife_report", "first_hand", "other"]
YESNO = ["yes", "no"]
AUTHOR = ["organisation", "individual"]
POST_FIELDS = {"relevant": YESNO, "sick_dead_local": YESNO, "content_class": CLASSES, "state_correct": YESNO}
ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


# ---------------------------------------------------------------- sample

def frame() -> pd.DataFrame:
    q = text(
        "SELECT f.platform, f.post_id, f.author_id, f.day, f.lang_detected AS lang, f.geo_state, f.step, "
        "f.label_source, f.relevant, f.content_class, f.local_place, f.illness, f.exclude, f.place_state, "
        "f.sentiment_set, t.author_type, t.news_like, p.text, p.author_name, p.author_handle, p.bio, "
        "p.owner_type, p.surface_type "
        "FROM journal.post_funnel f JOIN journal.post p USING (platform, post_id) "
        "JOIN journal.post_tag t USING (platform, post_id)")
    with local_engine().connect() as cn:
        d = pd.read_sql(q, cn)
    assert d.lang.isin(["en", "es"]).all() and not d.duplicated(["platform", "post_id"]).any()
    d["stratum"] = np.select(
        [d.step.isin([0, 15, 16]), d.label_source == "adjudicated", d.step.isin([9, 10])],
        ["sighting", "disagreement", "not_included"], default=d.content_class)
    assert d.stratum.isin(ALLOCATION).all()
    return d


def allocate(sizes: pd.Series) -> pd.Series:
    """sizes: population per (stratum, platform, lang). Returns the sample size per cell."""
    out = {}
    for s, total in ALLOCATION.items():
        cap = {c: int(sizes.get((s, *c), 0)) for c in CELL_SHARE}
        n = {c: 0 for c in CELL_SHARE}
        left = total
        while left > 0 and any(cap[c] > n[c] for c in CELL_SHARE):
            open_ = [c for c in CELL_SHARE if cap[c] > n[c]]
            share = sum(CELL_SHARE[c] for c in open_)
            want = {c: left * CELL_SHARE[c] / share for c in open_}
            give = {c: min(int(np.floor(want[c])), cap[c] - n[c]) for c in open_}
            if sum(give.values()) == 0:                      # largest remainder for the last few
                c = max(open_, key=lambda c: (want[c] - np.floor(want[c]), CELL_SHARE[c], c))
                give[c] = 1
            for c in open_:
                n[c] += give[c]
            left = total - sum(n.values())
        out.update({(s, *c): n[c] for c in CELL_SHARE})
    return pd.Series(out)


def sample() -> None:
    key_path = PRIVATE / "sample_key.parquet"
    assert not key_path.exists(), f"{key_path} exists; the sample is drawn once"
    d = frame()
    sizes = d.groupby(["stratum", "platform", "lang"]).size()
    n = allocate(sizes)
    rng = np.random.default_rng(SEED)
    picks = []
    for (s, plat, lang), k in n.items():
        if k == 0:
            continue
        cell = d[(d.stratum == s) & (d.platform == plat) & (d.lang == lang)].sort_values("post_id")
        take = cell.iloc[np.sort(rng.choice(len(cell), size=k, replace=False))]
        picks.append(take.assign(cell_population=len(cell), cell_sample=k, weight=len(cell) / k))
    smp = pd.concat(picks, ignore_index=True)
    assert len(smp) == sum(ALLOCATION.values()), f"sample is {len(smp)}"
    smp = smp.iloc[rng.permutation(len(smp))].reset_index(drop=True)
    smp["item"] = [f"P{i + 1:03d}" for i in range(len(smp))]
    authors = smp[["platform", "author_id"]].drop_duplicates()
    authors = authors.iloc[rng.permutation(len(authors))].reset_index(drop=True)
    authors["author_item"] = [f"A{i + 1:03d}" for i in range(len(authors))]
    smp = smp.merge(authors, on=["platform", "author_id"], validate="m:1")
    smp["llm_sighting"] = smp.step.isin([0, 15, 16])
    smp["llm_sick_dead_local"] = (smp.illness == "yes") & (smp.local_place == "yes") & (smp.exclude.fillna("") == "")
    PRIVATE.mkdir(parents=True, exist_ok=True)
    CODING.mkdir(parents=True, exist_ok=True)
    smp.to_parquet(key_path, index=False)
    design = (smp.groupby(["stratum", "platform", "lang"])
              .agg(population=("cell_population", "first"), sampled=("item", "size"), weight=("weight", "first"))
              .reset_index())
    design.to_csv(CODING / "sample_design.csv", index=False)
    smp[["item", "stratum", "platform", "lang", "weight"]].sort_values("item").to_csv(
        PRIVATE / "sample_weights.csv", index=False)     # per-item strata would unblind the coders
    pd.set_option("display.width", 200)
    print(design.to_string(index=False))
    print(f"{len(smp)} posts, {len(authors)} authors; reader-set population {int(design.population.sum()):,}")


# ---------------------------------------------------------------- packets

README = [
    ("What this is", "A blind check of the labels a language model gave to social-media posts about bird flu. "
                     "You code 400 posts and their authors. Nobody's labels are shown, including the model's."),
    ("Before you start", "Read CODER_GUIDE.md (one page, same folder). Work alone: do not discuss items with "
                         "the other coders, and do not look anything up."),
    ("Which cells to fill", "Only the yellow cells. Each has a drop-down list; pick one value. The grey row "
                            "marked EXAMPLE at the top of each sheet shows the format; it is not scored."),
    ("Sheet 'Posts'", "One row per post: the date it was published, its language, and its text (long posts are "
                      "cut at 1,500 characters). Fill relevant, sick_dead_local and content_class from the text "
                      "and the date only. Then look at geocoded_state and fill state_correct."),
    ("Sheet 'Authors'", "One row per account, with no posts. Fill author_type from the name, handle and bio "
                        "(X) or the name and Facebook type (Facebook)."),
    ("Unsure?", "Choose the best answer anyway; every yellow cell needs a value. Use the notes column to say "
                "what was unclear."),
    ("When you finish", "Save the file under the same name and send it to Nick. Do not rename sheets or "
                        "columns, and do not sort or delete rows."),
    ("Privacy", "The posts are public but the file should not be shared outside the team or quoted."),
]
EXAMPLE_POST = {"item": "EXAMPLE", "date": "2024-03-02", "language": "en",
                "text": "Dozens of dead geese at Silver Lake in Rochester, Minnesota this week. "
                        "City says avian flu is suspected.",
                "relevant": "yes", "sick_dead_local": "yes", "content_class": "wildlife_report",
                "geocoded_state": "MN", "state_correct": "yes", "notes": "made-up example"}
EXAMPLE_AUTHOR = {"author_item": "EXAMPLE", "platform": "X", "name": "KXYZ News 9", "handle": "@kxyznews",
                  "bio_or_facebook_type": "Local news, weather and sports for the valley.",
                  "author_type": "organisation", "notes": "made-up example"}


def clean(s) -> str:
    return ILLEGAL.sub(" ", "" if s is None or (isinstance(s, float) and np.isnan(s)) else str(s))


def write_book(path: Path, coder: str, posts: pd.DataFrame, authors: pd.DataFrame) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    font, bold = Font(name="Arial", size=10), Font(name="Arial", size=10, bold=True)
    yellow = PatternFill("solid", start_color="FFFF99")
    grey = PatternFill("solid", start_color="D9D9D9")
    head = PatternFill("solid", start_color="BDD7EE")
    wb = Workbook()
    ws = wb.active
    ws.title = "Read me"
    ws.append([f"Coding workbook for {coder}"])
    ws["A1"].font = Font(name="Arial", size=14, bold=True)
    for a, b in README:
        ws.append([a, b])
    for row in ws.iter_rows(min_row=2):
        row[0].font, row[1].font = bold, font
        row[1].alignment = row[0].alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 22, 110

    def sheet(name, example, rows, widths, inputs):
        s = wb.create_sheet(name)
        cols = list(example)
        s.append(cols)
        s.append([example[c] for c in cols])
        for r in rows:
            s.append([clean(r.get(c, "")) for c in cols])
        for j, c in enumerate(cols, start=1):
            letter = s.cell(row=1, column=j).column_letter
            s.column_dimensions[letter].width = widths.get(c, 14)
            s.cell(row=1, column=j).font, s.cell(row=1, column=j).fill = bold, head
            for i in range(2, s.max_row + 1):
                cell = s.cell(row=i, column=j)
                cell.font = font
                cell.alignment = Alignment(wrap_text=True, vertical="top")
                if isinstance(cell.value, str) and cell.value.startswith(("=", "+", "-", "@")):
                    cell.data_type = "s"                      # post text is never a formula
                if i == 2:
                    cell.fill = grey
                elif c in inputs:
                    cell.fill = yellow
            if c in inputs:
                dv = DataValidation(type="list", formula1='"' + ",".join(inputs[c]) + '"', allow_blank=True,
                                    showErrorMessage=True, errorTitle="Pick from the list",
                                    error="Choose one of: " + ", ".join(inputs[c]))
                s.add_data_validation(dv)
                dv.add(f"{letter}3:{letter}{s.max_row}")
        s.freeze_panes = "B2"

    sheet("Posts", EXAMPLE_POST, posts.to_dict("records"),
          {"item": 9, "date": 11, "language": 9, "text": 95, "relevant": 10, "sick_dead_local": 15,
           "content_class": 20, "geocoded_state": 15, "state_correct": 13, "notes": 30}, POST_FIELDS)
    sheet("Authors", EXAMPLE_AUTHOR, authors.to_dict("records"),
          {"author_item": 12, "platform": 10, "name": 34, "handle": 24, "bio_or_facebook_type": 80,
           "author_type": 14, "notes": 30}, {"author_type": AUTHOR})
    wb.save(path)


def packets() -> None:
    smp = pd.read_parquet(PRIVATE / "sample_key.parquet")
    posts = pd.DataFrame({
        "item": smp.item, "date": smp.day.astype(str), "language": smp.lang,
        "text": smp.text.fillna("").str[:MAX_CHARS], "geocoded_state": smp.geo_state})
    a = smp.drop_duplicates("author_item")
    fb = np.where(a.owner_type == "page", "Facebook Page", "Facebook profile (a personal account)")
    authors = pd.DataFrame({
        "author_item": a.author_item, "platform": np.where(a.platform == "x", "X", "Facebook"),
        "name": a.author_name, "handle": np.where(a.platform == "x", "@" + a.author_handle.fillna(""), ""),
        "bio_or_facebook_type": np.where(a.platform == "x", a.bio.fillna("").str[:300], fb)})
    CODING.mkdir(parents=True, exist_ok=True)
    for i, coder in enumerate(CODERS):
        rng = np.random.default_rng(SEED + 1 + i)
        write_book(CODING / f"coding_{coder}.xlsx", coder, posts.iloc[rng.permutation(len(posts))],
                   authors.iloc[rng.permutation(len(authors))])
        print(f"wrote coding_{coder}.xlsx: {len(posts)} posts, {len(authors)} authors")


# ---------------------------------------------------------------- analyse

def kappa(a, b, w=None) -> float:
    """Cohen's kappa, optionally weighted by sampling weight."""
    a, b = np.asarray(a, dtype=object), np.asarray(b, dtype=object)
    w = np.ones(len(a)) if w is None else np.asarray(w, dtype=float)
    cats = {c: i for i, c in enumerate(sorted(set(a) | set(b)))}
    m = np.zeros((len(cats), len(cats)))
    np.add.at(m, ([cats[x] for x in a], [cats[y] for y in b]), w)
    m = m / m.sum()
    po, pe = np.trace(m), (m.sum(axis=1) * m.sum(axis=0)).sum()
    return float((po - pe) / (1 - pe)) if pe < 1 else np.nan


def majority(df: pd.DataFrame) -> pd.Series:
    """The value at least two of the three coders gave; NaN when all three differ."""
    def one(r):
        v = r.value_counts()
        return v.index[0] if v.iloc[0] >= 2 else np.nan
    return df.apply(one, axis=1)


def boot(stat, d: pd.DataFrame, rng) -> tuple[float, float]:
    """Resample posts within sampling cells."""
    cells = [g.index.to_numpy() for _, g in d.groupby(["stratum", "platform", "lang"])]
    vals = []
    for _ in range(N_BOOT):
        ix = np.concatenate([rng.choice(c, len(c)) for c in cells])
        v = stat(d.iloc[ix].reset_index(drop=True))
        if np.isfinite(v):
            vals.append(v)
    return tuple(np.percentile(vals, [2.5, 97.5])) if vals else (np.nan, np.nan)


def read_coder(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    p = pd.read_excel(path, sheet_name="Posts", dtype=str).query("item != 'EXAMPLE'")
    a = pd.read_excel(path, sheet_name="Authors", dtype=str).query("author_item != 'EXAMPLE'")
    for f, allowed in POST_FIELDS.items():
        bad = p[~p[f].isin(allowed)]
        assert bad.empty, f"{path.name}: {len(bad)} posts with a blank or invalid {f} (first: {bad.item.iloc[0]})"
    bad = a[~a.author_type.isin(AUTHOR)]
    assert bad.empty, f"{path.name}: {len(bad)} authors with a blank or invalid author_type"
    return p.set_index("item"), a.set_index("author_item")


def retained(verbose: bool = True) -> pd.DataFrame:
    """The drawn sample against the reader set as it is now.

    The sample was drawn on 2026-11-18. On 2026-11-19 the English X posts of 2022-2024 were
    re-collected, and part of the sampled X posts left the reader set. Nothing is redrawn and the
    workbooks do not change. Returns the key (indexed by item) with, for each sampled post:

        in_corpus      the post is in the current reader set
        reread         its wording changed in the re-pull, so its current label comes from a later
                       reading of a text the coders did not see
        cur_*          its current stratum, labels, author type and geocoded state
        cur_weight     current cell population / sampled posts now in that cell

    The weight is a post-stratification weight: within a cell, the sampled posts that remain are
    treated as a random sample of the cell as it is now. That cell also holds posts that entered
    the reader set after the draw and could not have been sampled; `never_sampled` counts them.
    Writes docs/journal_rebuild/coding/sample_design_current.csv (counts only, no post text)."""
    key = pd.read_parquet(PRIVATE / "sample_key.parquet").set_index("item")
    now = frame()
    now["llm_sighting"] = now.step.isin([0, 15, 16])
    now["llm_sick_dead_local"] = ((now.illness == "yes") & (now.local_place == "yes")
                                  & (now.exclude.fillna("") == ""))
    with local_engine().connect() as cn:
        wave = pd.read_sql(text("SELECT platform, post_id, wave FROM journal.post_label"), cn)
    now = now.merge(wave, on=["platform", "post_id"], validate="1:1")
    cols = ["stratum", "relevant", "content_class", "llm_sick_dead_local", "llm_sighting",
            "sentiment_set", "author_type", "geo_state", "wave"]
    m = key.reset_index().merge(
        now[["platform", "post_id"] + cols].rename(columns={c: f"cur_{c}" for c in cols}),
        on=["platform", "post_id"], how="left", validate="1:1").set_index("item").loc[key.index]
    m["in_corpus"] = m.cur_stratum.notna()
    m["reread"] = m.cur_wave.isin(["wave2", "wave3"])
    cell = ["cur_stratum", "platform", "lang"]
    pop = now.groupby(["stratum", "platform", "lang"]).size().rename("population_now")
    kept = m[m.in_corpus].groupby(cell).size().rename("retained")
    drawn = key.groupby(["stratum", "platform", "lang"]).agg(
        population_at_draw=("cell_population", "first"), sampled=("weight", "size"))
    kept.index.names = pop.index.names
    design = pd.concat([drawn, pop, kept], axis=1).fillna(0).astype(int).reset_index()
    design["weight_now"] = (design.population_now / design.retained).where(design.retained > 0)
    new_ids = now[now.wave.isin(["wave2", "wave3"])].groupby(["stratum", "platform", "lang"]).size()
    design["never_sampled"] = [int(new_ids.get(tuple(r), 0)) for r in
                               design[["stratum", "platform", "lang"]].itertuples(index=False)]
    w = design.set_index(["stratum", "platform", "lang"]).weight_now
    m["cur_weight"] = [w.get((s, p, lg), np.nan) if ok else np.nan
                       for s, p, lg, ok in zip(m.cur_stratum, m.platform, m.lang, m.in_corpus)]
    CODING.mkdir(parents=True, exist_ok=True)
    design.to_csv(CODING / "sample_design_current.csv", index=False)
    if verbose:
        pd.set_option("display.width", 220, "display.max_rows", 100)
        print(design.to_string(index=False))
        by = m.groupby("platform").agg(sampled=("in_corpus", "size"), retained=("in_corpus", "sum"),
                                       reread=("reread", "sum"))
        print(by.to_string())
        moved = m[m.in_corpus & (m.cur_stratum != m.stratum)]
        print(f"retained posts whose stratum changed: {len(moved)}; "
              f"whose geocoded state changed: {int((m.in_corpus & (m.cur_geo_state != m.geo_state)).sum())}")
        empty = design[(design.population_now > 0) & (design.retained == 0)]
        covered = 1 - empty.population_now.sum() / design.population_now.sum()
        print(f"cells with posts now but no retained sample: {len(empty)} "
              f"({int(empty.population_now.sum()):,} posts); the weights cover {covered:.1%} of the reader set")
        thin = design[(design.retained > 0) & (design.retained < 5)]
        print(f"cells with 1 to 4 retained posts: {len(thin)}")
    return m


def analyse(folder: Path) -> None:
    import krippendorff

    key = pd.read_parquet(PRIVATE / "sample_key.parquet").set_index("item")
    cur = retained(verbose=False)
    books = {c: read_coder(folder / f"coding_{c}.xlsx") for c in CODERS}
    for c, (p, a) in books.items():
        assert set(p.index) == set(key.index), f"{c}: post items do not match the sample"
        assert set(a.index) == set(key.author_item), f"{c}: author items do not match the sample"
    rng = np.random.default_rng(SEED)
    rows = []

    def between(field: str, table: pd.DataFrame, scope: str) -> None:
        cats = sorted(pd.unique(table.to_numpy().ravel()))
        code = table.apply(lambda s: s.map({v: i for i, v in enumerate(cats)}))
        alpha = krippendorff.alpha(code.to_numpy().T.astype(float), level_of_measurement="nominal")
        rec = {"field": field, "scope": scope, "n": len(table), "krippendorff_alpha": alpha,
               "all_three_agree": float((table.nunique(axis=1) == 1).mean())}
        for x, y in itertools.combinations(CODERS, 2):
            rec[f"kappa_{x}_{y}"] = kappa(table[x], table[y])
        rows.append(rec)

    # agreement among the coders is about the coders, so it uses every coded item
    posts = {f: pd.DataFrame({c: books[c][0][f] for c in CODERS}).loc[key.index] for f in POST_FIELDS}
    # the human sighting: sick or dead animals at a local place, reported as wildlife or first-hand
    # (each coder's sheet is in its own order, so each column keeps its own index)
    posts["sighting"] = pd.DataFrame({
        c: ((books[c][0].sick_dead_local == "yes")
            & books[c][0].content_class.isin(["wildlife_report", "first_hand"])).map({True: "yes", False: "no"})
        for c in CODERS}).loc[key.index]
    for f, t in posts.items():
        between(f, t, "all posts")
    auth = pd.DataFrame({c: books[c][1].author_type for c in CODERS})
    plat = key.drop_duplicates("author_item").set_index("author_item").platform
    for scope, ids in (("all authors", auth.index), ("X authors", plat.index[plat == "x"]),
                       ("Facebook authors", plat.index[plat == "meta"])):
        between("author_type", auth.loc[ids], scope)
    pd.DataFrame(rows).to_csv(folder / "agreement_between_coders.csv", index=False)

    human = {f: majority(t) for f, t in posts.items()}
    am = majority(auth)
    out = []

    def versus(scope: str, d: pd.DataFrame, llm: dict, sentiment_set: pd.Series,
               author_ref: pd.Series) -> None:
        """Coder majority against the reader labels on the items in d (stratum, platform, lang,
        weight, author_item), with the reader labels in llm."""
        d = d.copy()
        for f, ref in llm.items():
            d["h"], d["m"] = human[f].loc[d.index], np.asarray(ref)
            x = d[d.h.notna()]
            k_stat = lambda g: kappa(g.h, g.m, g.weight)  # noqa: E731
            lo, hi = boot(k_stat, x.reset_index(drop=True), rng)
            rec = {"scope": scope, "field": f, "n": len(x), "no_majority": int(d.h.isna().sum()),
                   "agreement_weighted": float(np.average(x.h == x.m, weights=x.weight)),
                   "kappa_weighted": k_stat(x), "kappa_lo": lo, "kappa_hi": hi,
                   "kappa_unweighted": kappa(x.h, x.m)}
            if f in ("sighting", "sick_dead_local", "relevant"):
                # sensitivity: of the posts the coders call yes, the share the readers call yes;
                # PPV: of the posts the readers call yes, the share the coders call yes
                for name, base_col in (("sensitivity", "h"), ("ppv", "m")):
                    def ratio(g, base_col=base_col):
                        hit, base = (g.h == "yes") & (g.m == "yes"), g[base_col] == "yes"
                        return float(g.weight[hit].sum() / g.weight[base].sum()) if base.any() else np.nan
                    lo, hi = boot(ratio, x.reset_index(drop=True), rng)
                    rec.update({name: ratio(x), f"{name}_lo": lo, f"{name}_hi": hi,
                                f"{name}_n": int((x[base_col] == "yes").sum())})
            out.append(rec)
        # author type: the majority against the label used (one row per author; unweighted)
        a_plat = d.drop_duplicates("author_item").set_index("author_item").platform
        for name, ids in (("X authors", a_plat.index[a_plat == "x"]),
                          ("Facebook authors", a_plat.index[a_plat == "meta"])):
            if len(ids) == 0:
                continue
            out.append({"scope": scope, "field": f"author_type ({name})", "n": len(ids),
                        "agreement_weighted": float((am.loc[ids] == author_ref.loc[ids]).mean()),
                        "kappa_unweighted": kappa(am.loc[ids], author_ref.loc[ids])})
        # geocoder: share of posts whose geocoded state the majority judged correct
        d["h"] = human["state_correct"].loc[d.index]
        acc = lambda g: float(np.average(g.h == "yes", weights=g.weight))  # noqa: E731
        lo, hi = boot(acc, d.reset_index(drop=True), rng)
        out.append({"scope": scope, "field": "geocoded state correct (all posts in scope)", "n": len(d),
                    "agreement_weighted": acc(d), "kappa_lo": lo, "kappa_hi": hi})
        sent = d[np.asarray(sentiment_set, dtype=bool)]
        out.append({"scope": scope, "field": "geocoded state correct (posts readers placed in one US state)",
                    "n": len(sent), "agreement_weighted": acc(sent)})

    def labels(k: pd.DataFrame, pre: str) -> dict:
        return {"relevant": k[f"{pre}relevant"], "content_class": k[f"{pre}content_class"],
                "sick_dead_local": np.where(k[f"{pre}llm_sick_dead_local"].astype(bool), "yes", "no"),
                "sighting": np.where(k[f"{pre}llm_sighting"].astype(bool), "yes", "no")}

    base = ["stratum", "platform", "lang", "weight", "author_item"]
    # PRIMARY: the posts still in the reader set, the labels the analysis uses, weighted to the
    # reader set as it is now. A post whose wording changed in the re-pull is left out: the
    # coders saw the old text and the current label was read from the new one.
    c = cur[cur.in_corpus & ~cur.reread & (cur.cur_geo_state == cur.geo_state)]
    d = c[["cur_stratum", "platform", "lang", "cur_weight", "author_item"]].set_axis(base, axis=1)
    versus("current reader set, weighted", d, labels(c, "cur_"), c.cur_sentiment_set,
           c.drop_duplicates("author_item").set_index("author_item").cur_author_type)
    # SECONDARY: every coded post against the label it had when the sample was drawn, unweighted.
    # This is about the reading method only; it does not describe the current corpus.
    d = key[base].assign(weight=1.0)
    versus("all coded posts, labels at the draw, unweighted", d, labels(key, ""), key.sentiment_set,
           key.drop_duplicates("author_item").set_index("author_item").author_type)
    # the same, split by whether the post is still in the reader set
    for name, mask in (("coded posts still in the reader set, unweighted", cur.in_corpus),
                       ("coded posts no longer in the reader set, unweighted", ~cur.in_corpus)):
        k = key[mask.to_numpy()]
        versus(name, k[base].assign(weight=1.0), labels(k, ""), k.sentiment_set,
               k.drop_duplicates("author_item").set_index("author_item").author_type)

    pd.DataFrame(out).to_csv(folder / "agreement_with_readers.csv", index=False)
    pd.set_option("display.width", 250, "display.max_columns", 30, "display.max_rows", 200)
    print(f"sampled {len(key)}; in the current reader set {int(cur.in_corpus.sum())}; "
          f"of those re-read after a wording change (left out of the primary) "
          f"{int((cur.in_corpus & cur.reread).sum())}")
    print(pd.DataFrame(rows).round(3).to_string(index=False))
    print(pd.DataFrame(out).round(3).to_string(index=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["sample", "packets", "retained", "analyse"])
    ap.add_argument("--dir", default=str(CODING))
    a = ap.parse_args()
    if a.what == "analyse":
        analyse(Path(a.dir))
    else:
        {"sample": sample, "packets": packets, "retained": retained}[a.what]()
