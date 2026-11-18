"""Figures for the manuscript, from the result files in data/journal/models/.

    uv run --extra modeling python -m h5n1.journal.figures

Writes PNG (300 dpi) to docs/journal_rebuild/paper/figures/. Static figures for print: two
series (Facebook, X) in a validated two-hue pair, with marker shape as a second encoding and
direct labels. Every figure has one axis and a reference line at "no effect".
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from h5n1.journal import models as M  # noqa: E402

FIG = Path(__file__).resolve().parents[2] / "docs" / "journal_rebuild" / "paper" / "figures"
SERIES = {"meta": ("Facebook", "#2a78d6", "o"), "x": ("X", "#eb6834", "s")}
INK, MUTED, GRID, SURFACE = "#1f1f1e", "#6b6a64", "#e4e3de", "#ffffff"
DODGE = {"meta": -0.12, "x": 0.12}

plt.rcParams.update({
    "font.family": "Arial", "font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
    "xtick.color": MUTED, "ytick.color": MUTED, "text.color": INK, "axes.spines.top": False,
    "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.axisbelow": True, "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    "legend.frameon": False, "axes.titlesize": 10, "axes.titleweight": "bold", "axes.titlelocation": "left"})


def interval(ax, x, est, lo, hi, plat, label=None):
    name, color, marker = SERIES[plat]
    ax.errorbar(x, est, yerr=[[est - lo], [hi - est]], fmt=marker, color=color, ecolor=color, elinewidth=1.4,
                capsize=0, markersize=6, markeredgecolor=SURFACE, markeredgewidth=0.8, label=label, zorder=3)


def legend(ax, **kw):
    handles = [plt.Line2D([], [], marker=m, color=c, linestyle="", markersize=6, label=n) for n, c, m in SERIES.values()]
    ax.legend(handles=handles, loc=kw.pop("loc", "upper left"), handlelength=1, borderaxespad=0.2, **kw)


def save(fig, name):
    FIG.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG / name, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print("wrote", FIG / name)


def funnel():
    stages = [("Unique posts", {"meta": 301044, "x": 540225}), ("Reader set", {"meta": 25932, "x": 40658}),
              ("Sentiment set\n(Track A)", {"meta": 19342, "x": 33304}),
              ("Sighting posts", {"meta": 1670, "x": 3024}), ("Sightings\n(Track B)", {"meta": 505, "x": 827})]
    fig, ax = plt.subplots(figsize=(6.6, 3.6))
    for plat, (name, color, marker) in SERIES.items():
        y = [s[1][plat] for s in stages]
        ax.plot(range(len(stages)), y, color=color, marker=marker, markersize=6, linewidth=1.6,
                markeredgecolor=SURFACE, markeredgewidth=0.8, zorder=3)
        for i, v in enumerate(y):
            # X labels sit above-right of the marker and Facebook labels below-left, clear of both lines
            ax.annotate(f"{v:,}", (i, v), textcoords="offset points", fontsize=8, color=INK,
                        xytext=(5, 8) if plat == "x" else (-5, -13), ha="left" if plat == "x" else "right")
    ax.set_yscale("log")
    ax.set_ylim(200, 1.6e6)
    ax.set_xlim(-0.6, len(stages) - 0.4)
    ax.set_xticks(range(len(stages)), [s[0] for s in stages])
    ax.set_ylabel("Posts (log scale)")
    ax.grid(axis="x", visible=False)
    legend(ax, loc="upper right")
    save(fig, "fig1_funnel.png")


def track_a():
    L = pd.read_csv(M.OUT / "track_a_lift.csv")
    L = L[(L.comparison == "S4 - S3") & (L.variant == "primary") & (L.period == "test")]
    fig, axes = plt.subplots(1, 2, figsize=(6.6, 3.0), sharey=True)
    for ax, (sl, title) in zip(axes, (("new_onset", "New-onset state-weeks"), ("all", "All covered state-weeks"))):
        for plat in SERIES:
            for i, k in enumerate(M.KS):
                r = L[(L.platform == plat) & (L.slice == sl) & (L.k == k)].iloc[0]
                interval(ax, i + DODGE[plat], r.delta, r.lo, r.hi, plat)
        ax.axhline(0, color=MUTED, linewidth=0.9, zorder=1)
        ax.set_xticks(range(3), [f"{k} days" for k in M.KS])
        ax.set_xlim(-0.5, 2.5)
        ax.set_xlabel("Forecast horizon")
        ax.set_title(title)
        ax.grid(axis="x", visible=False)
    axes[0].set_ylabel("Change in PR-AUC from adding posts")
    legend(axes[1], loc="upper right")
    save(fig, "fig2_track_a.png")


def track_b():
    B = pd.read_csv(M.OUT / "track_b.csv")
    B = B[(B.gap_days == 0) & (B.slice == "all") & (B.period == "test")]
    fig, ax = plt.subplots(figsize=(4.4, 3.2))
    for plat in SERIES:
        for i, k in enumerate(M.KS):
            r = B[(B.platform == plat) & (B.k == k)].iloc[0]
            interval(ax, i + DODGE[plat], r.oe_ratio, r.lo, r.hi, plat)
    ax.axhline(1, color=MUTED, linewidth=0.9, zorder=1)
    ax.set_xticks(range(3), [f"{k} days" for k in M.KS])
    ax.set_xlim(-0.5, 2.5)
    ax.set_ylim(0.8, 1.7)
    ax.set_xlabel("Forecast horizon")
    ax.set_ylabel("Observed-to-expected ratio,\nsighted vs other state-weeks")
    ax.grid(axis="x", visible=False)
    legend(ax)
    save(fig, "fig3_track_b.png")


def mechanism():
    C = pd.read_csv(M.OUT / "mechanism_cells.csv")
    C = C[C.period == "test"]
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.3), gridspec_kw={"width_ratios": [1.15, 1]})
    ax = axes[0]
    gaps = [0, 3, 5, 7, 10]
    for plat in SERIES:
        for i, g in enumerate(gaps):
            r = C[(C.platform == plat) & (C.group == "all") & (C.gap_days == g)].iloc[0]
            interval(ax, i + DODGE[plat], r.oe_ratio, r.lo, r.hi, plat)
    ax.axhline(1, color=MUTED, linewidth=0.9, zorder=1)
    ax.set_xticks(range(len(gaps)), [str(g) for g in gaps])
    ax.set_xlim(-0.5, len(gaps) - 0.5)
    ax.set_xlabel("Days excluded from the start of the 14-day window")
    ax.set_ylabel("Observed-to-expected ratio")
    ax.set_title("A. All sightings, by days excluded")
    ax.grid(axis="x", visible=False)
    legend(ax)
    ax = axes[1]
    groups = [("wildlife_report", "Wildlife\nreport"), ("first_hand", "First-hand"),
              ("news_like", "News-like"), ("personal", "Personal")]
    for plat in SERIES:
        for i, (g, _) in enumerate(groups):
            r = C[(C.platform == plat) & (C.group == g) & (C.gap_days == 0)].iloc[0]
            interval(ax, i + DODGE[plat], r.oe_ratio, r.lo, r.hi, plat)
            ax.annotate(f"{int(r.sightings)}", (i + DODGE[plat], r.hi), textcoords="offset points", xytext=(0, 4),
                        ha="center", fontsize=8, color=INK)
    ax.axhline(1, color=MUTED, linewidth=0.9, zorder=1)
    ax.set_xticks(range(len(groups)), [g[1] for g in groups])
    ax.set_xlim(-0.5, len(groups) - 0.5)
    ax.set_title("B. By kind of post, no days excluded")
    ax.set_ylabel("Observed-to-expected ratio")
    ax.grid(axis="x", visible=False)
    ax.text(0.99, 0.02, "Numbers: sighted state-weeks", transform=ax.transAxes, ha="right", fontsize=8, color=MUTED)
    fig.tight_layout()
    save(fig, "fig4_mechanism.png")


if __name__ == "__main__":
    funnel()
    track_a()
    track_b()
    mechanism()
