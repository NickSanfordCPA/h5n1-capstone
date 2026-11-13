"""The two figures of docs/paper_data_results.md: X and Meta side by side, one figure per track.

    uv run --extra modeling python -m h5n1.models.paper_figures

Reads saved results only (no fitting):
  Track A  final_lift.csv (X) and explore2025_lift.csv (Meta): S4 - S3 PR-AUC, primary rows
  Track B  explore2025_v2guide_sightings.csv (X, Meta guide v2) and explore2025_sightings.csv (Meta)
Writes docs/figures/paper_track_a.png and docs/figures/paper_track_b.png.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402

from h5n1.models.x_baselines import ROOT  # noqa: E402

XO = ROOT / "data" / "X_derived" / "x_utility"
MO = ROOT / "data" / "Meta_derived" / "meta_utility"
FIG = ROOT / "docs" / "figures"
COLORS = {"X": "#2a78d6", "Meta": "#eb6834"}  # validated categorical slots 1-2, light surface
INK, MUTED, GRID = "#1f1f1e", "#6b6a65", "#e4e3df"


def track_a() -> pd.DataFrame:
    out = []
    for src, f in {"X": XO / "final_lift.csv", "Meta": MO / "explore2025_lift.csv"}.items():
        d = pd.read_csv(f)
        d = d[(d.variant == "primary") & (d.comparison == "S4 vs S3")]
        out.append(d.assign(src=src)[["src", "k", "slice", "delta", "d_lo", "d_hi"]])
    return pd.concat(out)


def track_b() -> pd.DataFrame:
    out = []
    for src, f in {"X": XO / "explore2025_v2guide_sightings.csv", "Meta": MO / "explore2025_sightings.csv"}.items():
        d = pd.read_csv(f)
        d = d[(d.gap_days == 0) & (d.slice == "all") & (d.period == "all")]
        out.append(d.assign(src=src, delta=d.oe_ratio, d_lo=d.ratio_lo, d_hi=d.ratio_hi, slice="all")
                   [["src", "k", "slice", "delta", "d_lo", "d_hi"]])
    return pd.concat(out)


def panel(ax, d: pd.DataFrame, null: float, xlabel: str, title: str) -> None:
    ks = [7, 14, 30]
    for i, src in enumerate(["X", "Meta"]):
        g = d[d.src == src].set_index("k").loc[ks]
        y = [j + (-0.14 if src == "X" else 0.14) for j in range(len(ks))]
        ax.hlines(y, g.d_lo, g.d_hi, color=COLORS[src], lw=2)
        ax.scatter(g.delta, y, s=64, color=COLORS[src], edgecolor="white", linewidth=2, zorder=3, label=src)
    ax.axvline(null, color=MUTED, lw=1, ls="--", zorder=0)
    ax.set_yticks(range(len(ks)), [f"k = {k}" for k in ks], color=INK)
    ax.set_ylim(len(ks) - 0.5, -0.5)
    ax.xaxis.set_major_locator(MaxNLocator(5))
    ax.set_xlabel(xlabel, color=INK)
    ax.set_title(title, loc="left", color=INK, fontsize=11)
    ax.grid(axis="x", color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(MUTED)
    ax.tick_params(colors=MUTED, length=0)


def main() -> None:
    plt.rcParams.update({"font.size": 10, "figure.facecolor": "#fcfcfb", "axes.facecolor": "#fcfcfb"})
    a = track_a()
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.4))
    for ax, sl, name in zip(axes, ["state_new_onset", "all"], ["New-onset state-weeks", "All state-weeks"]):
        panel(ax, a[a.slice == sl], 0.0, "Change in PR-AUC, S4 − S3 (95% CI)", name)
    fig.legend(*axes[0].get_legend_handles_labels(), frameon=False, loc="upper right", ncol=2)
    fig.tight_layout()
    fig.savefig(FIG / "paper_track_a.png", dpi=200)
    b = track_b()
    fig, ax = plt.subplots(figsize=(5.5, 3.2))
    panel(ax, b, 1.0, "O/E ratio after a sighting (95% CI)", "All sightings")
    fig.legend(*ax.get_legend_handles_labels(), frameon=False, loc="upper right", ncol=2)
    fig.tight_layout()
    fig.savefig(FIG / "paper_track_b.png", dpi=200)
    print(a.round(4).to_string(index=False))
    print(b.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
