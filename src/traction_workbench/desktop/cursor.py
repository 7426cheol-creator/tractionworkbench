"""Data cursor of a plot panel, as in a MATLAB / Simulink scope: pick a signal from the legend (or the panel's signal
list) and the cursor follows that curve, snapped to its plotted samples, with its value next to the marker, a time
cursor across every axes sharing the x axis and the value of every signal of the hovered axes at that instant in the
panel's readout.  Without a picked signal it follows the curve nearest the mouse.  A left click pins a cursor (two
pins give Δx and Δy), the arrow keys move the last pin one sample (Shift: ten), a right click or Esc removes the pins.

Presentation only: it reads the lines as they were drawn (their data) and never recomputes anything.  The moving
cursor is drawn by blitting (a mouse move does not redraw the figure); the pins are ordinary artists, so an export of
the figure carries them.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np
from matplotlib.lines import Line2D

from ..i18n import tr
from ..plots import style as S

_UNIT = re.compile(r"^(.*?)\s*\[([^\]]+)\]\s*$", re.S)
LIVE_COLOR = "#6e7781"
PIN_COLORS = ("#cf222e", "#0969da")
MAX_READOUT = 6


def split_label(text: str) -> tuple[str, str]:
    """``"time [ms]"`` -> ("time", "ms"); a label without a bracketed unit keeps its text and has no unit."""
    text = " ".join((text or "").split())
    m = _UNIT.match(text)
    return (m.group(1).strip(), m.group(2).strip()) if m else (text, "")


def num(v) -> str:
    if v is None:
        return "—"
    v = float(v)
    if math.isnan(v):
        return "—"
    if math.isinf(v):
        return "∞" if v > 0 else "−∞"
    if v != 0 and (abs(v) >= 1e7 or abs(v) < 1e-4):
        return f"{v:.4e}"
    return f"{v:.6g}"


def _with_unit(v, unit: str) -> str:
    return f"{num(v)} {unit}" if unit else num(v)


@dataclass
class Signal:
    """One plotted curve the cursor can follow."""
    ax: object
    ai: int                     # index of its axes in the figure (kept over a redraw)
    line: Line2D
    label: str
    x: np.ndarray
    y: np.ndarray
    mono: bool                  # x never decreases (a time trace): the cursor snaps by x
    drawstyle: str
    unit: str

    def index(self, x: float | None, disp=None) -> int | None:
        """The plotted sample the cursor snaps to: by x on a trace (a step line reads the sample in force at x), else
        the sample nearest on screen to ``disp`` (a curve in a plane)."""
        n = self.x.size
        if n == 0:
            return None
        if self.mono and x is not None:
            if self.drawstyle == "steps-post":
                i = int(np.searchsorted(self.x, x, side="right")) - 1
            elif self.drawstyle == "steps-pre":
                i = int(np.searchsorted(self.x, x, side="left"))
            else:
                i = int(np.searchsorted(self.x, x))
                if 0 < i < n and x - self.x[i - 1] <= self.x[i] - x:
                    i -= 1
            return min(max(i, 0), n - 1)
        if disp is None:
            return None
        pts = self.ax.transData.transform(np.column_stack([self.x, self.y]))
        d = np.hypot(pts[:, 0] - disp[0], pts[:, 1] - disp[1])
        d[~np.isfinite(d)] = np.inf
        return int(np.argmin(d)) if np.isfinite(d).any() else None

    def value_at(self, x: float) -> float | None:
        i = self.index(x) if self.mono else None
        return None if i is None else float(self.y[i])


def signals_of(fig) -> list[Signal]:
    """Every visible, labelled line drawn in data coordinates: reference lines (``axhline`` / ``axvline``, drawn
    partly in axes coordinates) and unlabelled helper lines are not signals."""
    out = []
    for ai, ax in enumerate(fig.axes):
        unit = split_label(ax.get_ylabel())[1]
        for ln in ax.get_lines():
            label = ln.get_label() or ""
            if label.startswith("_") or not ln.get_visible() or ln.get_transform() != ax.transData:
                continue
            try:
                x = np.asarray(ln.get_xdata(), dtype=float).ravel()
                y = np.asarray(ln.get_ydata(), dtype=float).ravel()
            except (TypeError, ValueError):                    # dates / categories: not read by the cursor
                continue
            if x.size == 0 or x.size != y.size:
                continue
            mono = bool(np.isfinite(x).all() and (x.size < 2 or np.all(np.diff(x) >= 0)))
            out.append(Signal(ax, ai, ln, " ".join(label.split()), x, y, mono, ln.get_drawstyle() or "default",
                              unit))
    return out


def display_names(sigs: list[Signal]) -> list[str]:
    """Signal names for the panel's list: the legend label, with its axes' quantity when a label repeats."""
    count = {}
    for s in sigs:
        count[s.label] = count.get(s.label, 0) + 1
    names = []
    for s in sigs:
        if count[s.label] == 1:
            names.append(s.label)
            continue
        q = split_label(s.ax.get_ylabel())[0] or split_label(s.ax.get_title())[0]
        names.append(f"{s.label} ({q})" if q else f"{s.label} ({tr('그래프', 'plot')} {s.ai + 1})")
    return names


def _siblings(ax) -> list:
    try:
        return list(ax.get_shared_x_axes().get_siblings(ax))
    except Exception:  # noqa: BLE001
        return [ax]


def _same_area(a, b) -> bool:
    return a.get_position().bounds == b.get_position().bounds


class DataCursor:
    """The cursor of one figure canvas (see the module docstring).  ``set_text`` receives the readout text,
    ``on_signals`` the signal names and the followed index after every redraw, ``on_track`` the followed index when a
    legend click changes it."""

    def __init__(self, canvas, set_text, on_signals=None, on_track=None):
        self.canvas = canvas
        self.set_text = set_text
        self.on_signals = on_signals
        self.on_track = on_track
        self.enabled = False
        self.signals: list[Signal] = []
        self.names: list[str] = []
        self.track: int | None = None           # the followed signal; None follows the one nearest the mouse
        self.pins: list[tuple[int, int]] = []   # (signal, sample) of the pinned cursors
        self.live: tuple[int, int] | None = None
        self._want = None                        # (label, axes index) of the followed signal, kept over a redraw
        self._want_pins = []                     # (label, axes index, x) of the pins, kept over a redraw
        self._legend = []                        # (legend entry artist, legend axes, label)
        self._legend_texts = {}                  # label -> legend texts showing it
        self._art = []                           # animated artists of the moving cursor: time lines, marker, note
        self._pin_art = []
        self._bg = None
        canvas.mpl_connect("draw_event", self._on_draw)
        canvas.mpl_connect("key_press_event", self._on_key)
        canvas.mpl_connect("figure_leave_event", lambda _ev: self.hide())

    @property
    def fig(self):
        return self.canvas.figure

    # -- life cycle ------------------------------------------------------------------------------------------------
    def clear(self):
        """The figure was replaced by a note: nothing to follow (the choice is kept for the next figure)."""
        self._remember()
        self.signals, self.names, self.pins, self.live = [], [], [], None
        self.track = None
        self._art, self._pin_art, self._legend, self._legend_texts, self._bg = [], [], [], {}, None
        if self.on_signals:
            self.on_signals([], None)

    def rebuild(self):
        """After the panel drew a figure: collect its signals and legend entries and keep the followed signal and the
        pins (by label and x) when the new figure still shows them."""
        self._remember()
        self._art, self._pin_art, self._bg, self.live = [], [], None, None
        self.signals = signals_of(self.fig)
        self.names = display_names(self.signals)
        key = {(s.label, s.ai): k for k, s in enumerate(self.signals)}
        self.track = key.get(self._want) if self._want else None
        self.pins = []
        for label, ai, x in self._want_pins:
            k = key.get((label, ai))
            i = None if k is None else self.signals[k].index(x)
            if i is not None:
                self.pins.append((k, i))
        self._legend, self._legend_texts = [], {}
        for lg in [a.get_legend() for a in self.fig.axes] + list(getattr(self.fig, "legends", [])):
            if lg is None:
                continue
            for h, t in zip(getattr(lg, "legend_handles", None) or [], lg.get_texts()):
                label = " ".join(t.get_text().split())
                for art in (h, t):
                    if art is not None:
                        self._legend.append((art, lg.axes, label))
                self._legend_texts.setdefault(label, []).append(t)
        for s in self.signals:
            s.line._twb_lw = s.line.get_linewidth()
            s.line._twb_z = s.line.get_zorder()
        self._style_track()
        if self.enabled and self.pins:
            self._draw_pins()
        if self.on_signals:
            self.on_signals(self.names, self.track)

    def _remember(self):
        if self.signals:
            s = self.signals[self.track] if self.track is not None and self.track < len(self.signals) else None
            self._want = None if s is None else (s.label, s.ai)
            self._want_pins = [(self.signals[k].label, self.signals[k].ai, float(self.signals[k].x[i]))
                               for k, i in self.pins if k < len(self.signals)]

    def set_enabled(self, on: bool):
        self.enabled = bool(on)
        self.live = None
        for a in self._art:
            a.set_visible(False)
        if not on:
            self._remove_pins()
            self.pins = []
            self.set_text("")
        else:
            self._draw_pins()
        self._style_track()
        self.canvas.draw_idle()

    def select(self, k: int | None):
        """Follow signal ``k`` (None: the signal nearest the mouse)."""
        self.track = k if k is not None and 0 <= k < len(self.signals) else None
        self._style_track()
        self.canvas.draw_idle()
        if self.on_track:
            self.on_track(self.track)

    def _style_track(self):
        for k, s in enumerate(self.signals):
            lw, z = getattr(s.line, "_twb_lw", s.line.get_linewidth()), getattr(s.line, "_twb_z", s.line.get_zorder())
            on = self.enabled and k == self.track
            s.line.set_linewidth(lw * 1.8 + 0.6 if on else lw)
            s.line.set_zorder(max(z, 4) if on else z)
        name = self.signals[self.track].label if self.enabled and self.track is not None else None
        for label, texts in self._legend_texts.items():
            for t in texts:
                t.set_fontweight("bold" if label == name else "normal")

    # -- mouse and keys ------------------------------------------------------------------------------------------------
    def legend_hit(self, ev) -> int | None:
        """The signal whose legend entry (line or text) is under the mouse; in the legend's axes or its twins first,
        else anywhere in the figure.  (Picking is done here: matplotlib picks only in the topmost axes of a twin.)"""
        for art, owner, label in self._legend:
            try:
                if not art.get_visible() or not art.contains(ev)[0]:
                    continue
            except Exception:  # noqa: BLE001 - an entry that cannot be hit-tested is not pickable
                continue
            near = [a for a in self.fig.axes if owner is not None and (a is owner or _same_area(a, owner))]
            for pool in (near, list(self.fig.axes)):
                k = next((j for j, s in enumerate(self.signals) if s.ax in pool and s.label == label), None)
                if k is not None:
                    return k
            return -1                                   # an entry without a curve (a band, a limit line)
        return None

    def press(self, ev) -> bool:
        """A mouse press with the cursor on: a legend entry selects its signal (again: back to the nearest), a left
        click elsewhere pins the cursor (a third pin starts a new pair), a right click removes the pins.  True when
        the press was used."""
        if not self.enabled:
            return False
        k = self.legend_hit(ev)
        if k is not None:
            if k >= 0:
                self.select(None if k == self.track else k)
            return True
        if ev.button == 3:
            self.clear_pins()
            return True
        if ev.button != 1 or ev.inaxes is None:
            return False
        if self.live is None:
            self.move(ev)
        if self.live is None:
            return False
        self.pins = (self.pins if len(self.pins) < 2 else []) + [self.live]
        self._draw_pins()
        self.canvas.draw_idle()
        self._readout()
        return True

    def move(self, ev):
        """Mouse move: snap to the followed (or the nearest) signal; the moving cursor and the readout follow."""
        if not self.enabled or ev.inaxes is None or ev.xdata is None or not self.signals:
            return
        k = self._pick_signal(ev)
        if k is None:
            self.hide()
            return
        s = self.signals[k]
        i = s.index(ev.xdata if s.mono else None, (ev.x, ev.y))
        if i is None:
            self.hide()
            return
        self.live = (k, i)
        self._show_live(ev.inaxes)

    def _stack(self, ev) -> list:
        """The hovered axes and the axes drawn over the same area (twins)."""
        return [a for a in self.fig.axes if a.bbox.contains(ev.x, ev.y)] or [ev.inaxes]

    def _pick_signal(self, ev) -> int | None:
        stack = self._stack(ev)
        if self.track is not None:
            s = self.signals[self.track]
            if (s.mono and s.ax in _siblings(ev.inaxes)) or s.ax in stack:
                return self.track
        best, bd = None, np.inf
        for k, s in enumerate(self.signals):
            if s.ax not in stack:
                continue
            i = s.index(ev.xdata if s.mono else None, (ev.x, ev.y))
            if i is None or not (np.isfinite(s.y[i]) and np.isfinite(s.x[i])):
                continue
            px, py = s.ax.transData.transform((s.x[i], s.y[i]))
            d = abs(py - ev.y) if s.mono else math.hypot(px - ev.x, py - ev.y)
            if d < bd:
                best, bd = k, d
        return best

    def clear_pins(self):
        self.pins = []
        self._remove_pins()
        self.canvas.draw_idle()
        self._readout()

    def step(self, n: int):
        """Move the last pin (else the moving cursor) ``n`` samples along its signal."""
        if self.pins:
            k, i = self.pins[-1]
            self.pins[-1] = (k, int(min(max(i + n, 0), self.signals[k].x.size - 1)))
            self._draw_pins()
            self.canvas.draw_idle()
            self._readout()
        elif self.live is not None:
            k, i = self.live
            self.live = (k, int(min(max(i + n, 0), self.signals[k].x.size - 1)))
            self._show_live(None)

    def _on_key(self, ev):
        if not self.enabled:
            return
        if ev.key == "escape":
            self.clear_pins()
            return
        n = {"left": -1, "right": 1, "shift+left": -10, "shift+right": 10}.get(ev.key)
        if n is not None:
            self.step(n)

    def hide(self):
        if not any(a.get_visible() for a in self._art):
            return
        for a in self._art:
            a.set_visible(False)
        self._blit()

    # -- drawing -------------------------------------------------------------------------------------------------------
    def _on_draw(self, ev):
        if not self.enabled or getattr(ev, "canvas", self.canvas) is not self.canvas:
            return                                   # (a figure export draws on another canvas: not our background)
        try:
            self._bg = self.canvas.copy_from_bbox(self.fig.bbox)
            for a in self._art:
                if a.get_visible():
                    self.fig.draw_artist(a)
        except Exception:  # noqa: BLE001 - a canvas without blitting shows no moving cursor
            self._bg = None

    def _ensure_live(self, ax):
        """The moving cursor's artists: one time line per axes, the marker and its note in ``ax``."""
        t = S.theme()
        if not self._art:
            for a in self.fig.axes:
                ln = Line2D([0, 0], [0, 1], transform=a.get_xaxis_transform(), color=LIVE_COLOR, lw=0.9, ls="--",
                            animated=True, visible=False, label="_cursor")
                ln.set_in_layout(False)
                a.add_artist(ln)
                self._art.append(ln)
        n = len(self.fig.axes)
        if len(self._art) == n + 2 and self._art[n].axes is ax:
            return
        for a in self._art[n:]:
            try:
                a.remove()
            except (ValueError, NotImplementedError, AttributeError):
                pass
        del self._art[n:]
        marker = Line2D([0], [0], marker="o", ms=8, mfc="none", mew=1.8, ls="none", animated=True, visible=False,
                        label="_cursor")
        marker.set_in_layout(False)
        ax.add_artist(marker)
        note = ax.annotate("", xy=(0, 0), xytext=(12, 12), textcoords="offset points", fontsize=7.5, color=t["fg"],
                           animated=True, visible=False, zorder=30, annotation_clip=False,
                           bbox=dict(boxstyle="round,pad=0.35", fc=t["panel"], ec=LIVE_COLOR, alpha=0.95))
        note.set_in_layout(False)
        self._art += [marker, note]

    def _show_live(self, hovered):
        k, i = self.live
        s = self.signals[k]
        x, y = float(s.x[i]), float(s.y[i])
        self._ensure_live(s.ax)
        n = len(self.fig.axes)
        group = _siblings(s.ax) if s.mono else []
        for j, (a, ln) in enumerate(zip(self.fig.axes, self._art[:n])):
            show = a in group and a.get_visible() and not any(_same_area(a, b) for b in self.fig.axes[:j])
            ln.set_visible(show)
            if show:
                ln.set_xdata([x, x])
        marker, note = self._art[n], self._art[n + 1]
        marker.set_data([x], [y])
        marker.set_markeredgecolor(s.line.get_color())
        marker.set_visible(bool(np.isfinite(y)))
        xname, xunit = self.xlabel(s.ax)
        note.xy = (x, y)
        note.set_text(f"{s.label}\n{xname or 'x'} = {_with_unit(x, xunit)}\ny = {_with_unit(y, s.unit)}")
        _place(note, s.ax, x, y)
        note.set_visible(bool(np.isfinite(y)))
        self._blit()
        self._readout(hovered)

    def xlabel(self, ax) -> tuple[str, str]:
        for a in [ax] + [b for b in _siblings(ax) if b is not ax]:
            name, unit = split_label(a.get_xlabel())
            if name or unit:
                return name, unit
        return "", ""

    def _blit(self):
        if self._bg is None:
            return
        try:
            self.canvas.restore_region(self._bg)
            for a in self._art:
                if a.get_visible():
                    self.fig.draw_artist(a)
            self.canvas.blit(self.fig.bbox)
        except Exception:  # noqa: BLE001 - presentation only
            pass

    def _remove_pins(self):
        for a in self._pin_art:
            try:
                a.remove()
            except (ValueError, NotImplementedError, AttributeError):
                pass
        self._pin_art = []

    def _draw_pins(self):
        self._remove_pins()
        t = S.theme()
        for n, (k, i) in enumerate(self.pins):
            if k >= len(self.signals):
                continue
            s = self.signals[k]
            x, y = float(s.x[i]), float(s.y[i])
            col = PIN_COLORS[n % len(PIN_COLORS)]
            if s.mono:
                axes = self.fig.axes
                for j, a in enumerate(axes):
                    if a not in _siblings(s.ax) or any(_same_area(a, b) for b in axes[:j]):
                        continue
                    ln = Line2D([x, x], [0, 1], transform=a.get_xaxis_transform(), color=col, lw=1.0, ls="-.",
                                label="_cursor", zorder=25)
                    ln.set_in_layout(False)
                    a.add_artist(ln)
                    self._pin_art.append(ln)
            mk = Line2D([x], [y], marker="o", ms=6, color=col, ls="none", label="_cursor", zorder=26)
            mk.set_in_layout(False)
            s.ax.add_artist(mk)
            xunit = self.xlabel(s.ax)[1]
            note = s.ax.annotate(f"{n + 1}: {_with_unit(x, xunit)} · {_with_unit(y, s.unit)}", xy=(x, y),
                                 xytext=(8, 8), textcoords="offset points", fontsize=7.5, color=col, zorder=27,
                                 annotation_clip=False,
                                 bbox=dict(boxstyle="round,pad=0.25", fc=t["panel"], ec=col, alpha=0.95))
            note.set_in_layout(False)
            _place(note, s.ax, x, y, below=bool(n))
            self._pin_art += [mk, note]

    # -- readout -------------------------------------------------------------------------------------------------------
    def pin_text(self) -> str:
        if not self.pins:
            return ""
        parts = []
        for n, (k, i) in enumerate(self.pins):
            s = self.signals[k]
            xunit = self.xlabel(s.ax)[1]
            parts.append(f"{n + 1}: {s.label} @ {_with_unit(s.x[i], xunit)} = {_with_unit(s.y[i], s.unit)}")
        if len(self.pins) == 2:
            (k1, i1), (k2, i2) = self.pins
            a, b = self.signals[k1], self.signals[k2]
            xunit = self.xlabel(b.ax)[1]
            dx, dy = float(b.x[i2] - a.x[i1]), float(b.y[i2] - a.y[i1])
            parts.append(f"Δx = {_with_unit(dx, xunit)} · Δy = {_with_unit(dy, b.unit if a.unit == b.unit else '')}")
            if dx and a.unit == b.unit:
                slope = f"{b.unit}/{xunit}" if b.unit and xunit else ""
                parts.append(f"Δy/Δx = {_with_unit(dy / dx, slope)}")
        return "   ".join(parts)

    def live_text(self, hovered=None) -> str:
        if self.live is None:
            return ""
        k, i = self.live
        s = self.signals[k]
        x = float(s.x[i])
        xname, xunit = self.xlabel(s.ax)
        if not s.mono:
            return f"▶ {s.label}: x = {_with_unit(x, xunit)} · y = {_with_unit(s.y[i], s.unit)}"
        stack = [a for a in self.fig.axes if a is s.ax or _same_area(a, s.ax)]
        if hovered is not None and hovered in _siblings(s.ax):
            stack += [a for a in self.fig.axes if _same_area(a, hovered) and a not in stack]
        shown = [k] + [j for j, o in enumerate(self.signals) if j != k and o.ax in stack and o.mono]
        row = [f"{xname or 'x'} = {_with_unit(x, xunit)}"]
        for j in shown[:MAX_READOUT]:
            o = self.signals[j]
            v = float(o.y[i]) if j == k else o.value_at(x)
            row.append(f"{'▶ ' if j == k else ''}{o.label} = {_with_unit(v, o.unit)}")
        if len(shown) > MAX_READOUT:
            row.append(tr(f"외 {len(shown) - MAX_READOUT}개", f"+{len(shown) - MAX_READOUT} more"))
        return "  │  ".join(row)

    def _readout(self, hovered=None):
        self.set_text("\n".join(x for x in (self.live_text(hovered), self.pin_text()) if x))


def _place(note, ax, x, y, below: bool = False):
    """Keep a note inside its axes: left of the marker in the right third, below it in the top quarter; ``below``
    (the second pin) puts it below the marker unless that is near the bottom, so it never sits on the first pin's."""
    right = top = low = False
    try:
        px, py = ax.transData.transform((x, y))
        bb = ax.bbox
        right = px > bb.x0 + 0.66 * bb.width
        top = py > bb.y0 + 0.72 * bb.height
        low = py < bb.y0 + 0.28 * bb.height
    except Exception:  # noqa: BLE001 - presentation only
        pass
    if below:
        top = not low
    note.set_position((-12 if right else 12, -12 if top else 12))
    note.set_ha("right" if right else "left")
    note.set_va("top" if top else "bottom")
