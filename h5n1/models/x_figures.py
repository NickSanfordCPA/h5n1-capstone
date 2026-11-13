"""Draw the two figures in docs/x_utility_results.md from the saved final_*.csv results.

Fits nothing; reads data/X_derived/x_utility/ and writes docs/figures/.
Run: uv run --extra modeling python -m h5n1.models.x_figures
"""

import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
R = ROOT / "data" / "X_derived" / "x_utility"
OUT = ROOT / "docs" / "figures"
OUT.mkdir(parents=True, exist_ok=True)
BLUE, ORANGE, INK, MUTED, GRID, SURFACE = (
    "#2a78d6",
    "#eb6834",
    "#0b0b0b",
    "#52514e",
    "#e4e3df",
    "#fcfcfb",
)
plt.rcParams.update(
    {
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "axes.edgecolor": GRID,
        "text.color": INK,
        "axes.labelcolor": MUTED,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "font.size": 11,
    }
)
lift = pd.read_csv(R / "final_lift.csv")
sight = pd.read_csv(R / "final_sightings.csv")


def forest(ax, rows, x, lo, hi, null, xlabel, title):
    y = list(range(len(rows)))[::-1]
    ax.axvline(null, color=MUTED, lw=1)
    ax.hlines(y, rows[lo], rows[hi], color=BLUE, lw=2)
    ax.scatter(rows[x], y, s=64, color=BLUE, edgecolor=SURFACE, linewidth=2, zorder=3)
    ax.set_yticks(y, rows["label"])
    ax.set_xlabel(xlabel)
    ax.set_title(title, loc="left", fontsize=12, color=INK)
    ax.grid(axis="x", color=GRID, lw=0.8)
    ax.set_axisbelow(True)


p = lift[(lift.variant == "primary") & (lift.comparison == "S4 vs S3")].copy()
names = {"all": "all", "state_new_onset": "new onset"}
p["label"] = "k=" + p.k.astype(str) + ", " + p.slice.map(names)
s = sight[sight.period == "all"].copy()
s["label"] = (
    "k=" + s.k.astype(str) + ", " + s.slice.map(names) + s.gap_days.map({0: "", 5: ", 5-day gap"})
)
fig, (left, right) = plt.subplots(1, 2, figsize=(12, 4.2))
forest(left, p, "delta", "d_lo", "d_hi", 0, "S4 − S3 PR-AUC", "Sentiment and volume, every horizon")
forest(
    right,
    s,
    "oe_ratio",
    "ratio_lo",
    "ratio_hi",
    1,
    "Observed vs expected ratio",
    "Sightings, every horizon",
)
fig.tight_layout()
fig.savefig(OUT / "x_utility_all_horizons.png", dpi=170)
plt.close(fig)

et = pd.read_csv(R / "event_time.csv")
fig, ax = plt.subplots(figsize=(8.4, 4.2))
ax.axvline(0, color=GRID, lw=1)
ax.axhline(0, color=MUTED, lw=1)
for col, c, name in [
    ("x_share_7d", BLUE, "State share of posts"),
    ("x_sent_7d", ORANGE, "Sentiment"),
]:
    ax.plot(
        et.weeks_from_onset,
        et[col],
        color=c,
        lw=2,
        marker="o",
        ms=8,
        mec=SURFACE,
        mew=2,
        label=name,
    )
    ax.annotate(
        name,
        (et.weeks_from_onset.iloc[-1], et[col].iloc[-1]),
        xytext=(8, 0),
        textcoords="offset points",
        color=INK,
        va="center",
    )
ax.legend(frameon=False, loc="upper left")
ax.set_xlabel("Weeks from state onset")
ax.set_ylabel("Mean z-score")
ax.set_title(
    "X volume peaks at onset; sentiment barely moves (2022-24)", loc="left", fontsize=12, color=INK
)
ax.set_xlim(-6.3, 6.6)
fig.tight_layout()
fig.savefig(OUT / "x_utility_event_time.png", dpi=170)
plt.close(fig)
print("ok")
