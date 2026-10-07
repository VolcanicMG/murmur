"""Settings > Speech: how the user's speaking changes over time (metrics from murmur.speech, computed from what murmur heard).
Pure helpers (METRICS, improving, delta_text) are unit-tested; the page builder imports tk lazily."""
from . import store

NEEDED = 50
BAND = (130, 160)  # steady pace, words per minute
# key (a murmur.speech goal): (name, unit, goal chip label, better, decimals); better is "down" | "up" | "band"
METRICS = {
    "fillers": ("Filler words", "per 100 words", "Fewer fillers", "down", 1),
    "pace": ("Pace", "words per minute", "Steadier pace", "band", 0),
    "corrections": ("Self-corrections", "per 100 words", "Fewer self-corrections", "down", 1),
    "pauses": ("Pauses", "per 20 words", "Fewer pauses", "down", 2),
    "clarity": ("Cleanup distance", "0 = nothing to clean", "Clearer sentences", "down", 2),
    "sentence_length": ("Sentence length", "words", "Shorter sentences", "down", 1),
}


def improving(metric, current, baseline, delta_pct):
    """True / False / None (steady or unknown) for how `current` moved against `baseline` for this metric's notion of better."""
    better = METRICS[metric][3]
    if current is None or baseline is None:
        return None
    if better == "band":
        dist = lambda v: 0 if BAND[0] <= v <= BAND[1] else min(abs(v - BAND[0]), abs(v - BAND[1]))
        if dist(current) == 0:
            return None
        return dist(current) < dist(baseline)
    if not delta_pct:
        return None
    return delta_pct < 0 if better == "down" else delta_pct > 0


def delta_text(metric, current, baseline, delta_pct):
    """"steady" inside the pace band, else an arrow and the absolute percentage ("▼ 34%"); "" when there is no comparison."""
    if metric == "pace" and current is not None and BAND[0] <= current <= BAND[1]:
        return "steady"
    if delta_pct is None or abs(delta_pct) < 0.5:
        return "no change" if delta_pct is not None else ""
    return f"{'▼' if delta_pct < 0 else '▲'} {abs(delta_pct):.0f}%"


def fmt(v, metric="fillers"):
    """Value with the metric's decimals; an en dash for None."""
    return "\u2013" if v is None else f"{v:.{METRICS[metric][4]}f}"


def rows_for(rows, app):
    """History rows for one app (None / "All apps" = all)."""
    return rows if not app or app == "All apps" else [r for r in rows if r.get("app") == app]


def page(content, ui, cfg, after_save):
    """(page, load) for the sidebar; load() re-reads history and recomputes everything."""
    import tkinter as tk
    from . import render
    from .inputs import Select
    from .settings import Panel, _armed_pill, _label, _page, _scroll_frame
    from .theme import ACCENT, AMBER, FIELD, FILL, HAIR, NAME, SECONDARY, SUCCESS, TEXT, INPUT, blend, on_fill
    from .widgets import Toggle, _Round, put
    k = ui.k
    pg = _page(content, ui, "Speech", "How you speak, measured from what murmur heard, not from the cleaned text.")
    inner = _scroll_frame(pg.body)
    st = {"open": None, "rows": [], "app": "All apps"}

    class GoalChip(_Round):
        """Multi-select chip: tinted + accent text when on."""

        def __init__(self, parent, key, on, command):
            super().__init__(parent, ui, 30, cursor="hand2")
            self.key, self.on, self.text = key, on, METRICS[key][2]
            self.config(width=ui.fonts["chip"].measure(self.text) + k(34))
            self.bind("<Button-1>", lambda e: command(self))
            self.bind("<Enter>", lambda e: self._hover(True)); self.bind("<Leave>", lambda e: self._hover(False))

        def _hover(self, on):
            self.hover = on
            self.draw()

        def paint(self):
            w, h = self.winfo_width(), self.winfo_height()
            if w < 2:
                return
            fill = blend(ACCENT, FIELD, 0.30) if self.on else on_fill(0.14 if self.hover else 0.09)
            put(self, render.rounded((w, h), k(15), fill), 0, 0)
            self.create_text(w / 2, h / 2, text=self.text, fill=ACCENT if self.on else NAME, font=ui.fonts["chip"])

    # ---- goals
    goals_panel = Panel(inner, ui, "Goals"); goals_panel.pack(fill="x", pady=(0, k(12)))
    chip_row = tk.Frame(goals_panel.rows, bg=FIELD); chip_row.pack(fill="x")
    chips = {}

    def goals():
        return [m for m, c in chips.items() if c.on]

    def save(patch):
        try:
            c = store.load_config()
            c.setdefault("coach", {}).update(patch)
            store.save_config(c)
            after_save()
        except (ValueError, OSError):
            pass  # the status line of the open window reports save failures through after_save
        render_all()

    def flip(chip):
        chip.on = not chip.on
        chip.draw()
        save({"goals": goals()})
    for i, m in enumerate(METRICS):
        chips[m] = GoalChip(chip_row, m, False, flip)
        chips[m].grid(row=i // 3, column=i % 3, padx=(0, k(8)), pady=(0, k(8)), sticky="w")
    hrow = tk.Frame(goals_panel.rows, bg=FIELD); hrow.pack(fill="x", pady=(k(4), 0))
    hint = Toggle(hrow, ui); hint.pack(side="left")
    _label(hrow, ui, "Show a hint on the card after each dictation", "body", NAME, FIELD).pack(side="left", padx=(k(12), 0))
    hint.bind("<Button-1>", lambda e: hint.after(1, lambda: save({"card_hint": hint.get()})), add="+")

    # ---- app filter
    frow = tk.Frame(inner, bg=FILL); frow.pack(fill="x", pady=(0, k(12)))
    _label(frow, ui, "App", "body", NAME).pack(side="left", padx=(0, k(12)))
    app_sel = Select(frow, ui, ["All apps"]); app_sel.pack(side="left")
    body = tk.Frame(inner, bg=FILL); body.pack(fill="x")
    app_sel.var.trace_add("write", lambda *_: (st.__setitem__("app", app_sel.get()), render_all()))

    # ---- rendering
    def clear():
        for w in body.winfo_children():
            w.destroy()

    def collecting(n, needed):
        p = Panel(body, ui, f"Collecting: {n} of {needed} dictations"); p.pack(fill="x", pady=(0, k(12)))
        bar = tk.Canvas(p.rows, height=k(10), bg=FIELD, highlightthickness=0, bd=0); bar.pack(fill="x", pady=(0, k(10)))

        def draw(e=None):
            w = bar.winfo_width()
            bar.delete("all"); bar._imgs = []
            if w > 2:
                img = render.pill((w, k(10)), INPUT).copy()
                img.alpha_composite(render.pill((max(k(10), round(w * min(1, n / needed))), k(10)), ACCENT), (0, 0))
                put(bar, img, 0, 0)
        bar.bind("<Configure>", draw)
        _label(p.rows, ui, "Trends need a couple of weeks of normal use. murmur keeps a few speech measurements with each dictation "
               "and compares your recent days with your earlier baseline.", "small", SECONDARY, FIELD).config(wraplength=k(560), justify="left")
        p.rows.winfo_children()[-1].pack(anchor="w")

    def tile(parent, speech, rows, metric, big):
        name, unit = METRICS[metric][:2]
        tr = speech.trend(rows, metric) or {}
        cur, base, d = tr.get("current"), tr.get("baseline"), tr.get("delta_pct")
        ok = bool(tr.get("enough"))
        better = (improving(metric, cur, base, d) if tr.get("better") is None else tr["better"]) if ok else None
        col = SUCCESS if better else AMBER if better is False else SECONDARY
        w, h = k(196), k(112 if big else 88)
        c = tk.Canvas(parent, width=w, height=h, bg=parent.cget("bg"), highlightthickness=0, bd=0, cursor="hand2")
        pts = tuple(v for _, v in speech.series(rows, metric, 14)) if ok else ()
        img = render.rounded((w, h), k(12), FIELD).copy()
        top, bot = k(66), h - k(8)  # own row below the value line (value text ends by ~y 58)
        sh = min(bot - top, k(30))
        if pts:
            img.alpha_composite(render.sparkline(pts, (k(80), sh), col, base), (w - k(80) - k(12), bot - sh))
        put(c, img, 0, 0)
        c.create_text(k(14), k(18), text=name, anchor="w", fill=SECONDARY, font=ui.fonts["label"])
        c.create_text(k(14), k(44), text=(fmt(cur, metric) if ok else "–"), anchor="w", fill=TEXT, font=ui.fonts["tile"])
        c.create_text(k(14) + ui.fonts["tile"].measure(fmt(cur, metric) if ok else "–") + k(6), k(48), text=unit if ok else "", anchor="w", fill=SECONDARY, font=ui.fonts["label"])
        sub = f"baseline {fmt(base, metric)}" if ok and base is not None else "not enough data yet"
        c.create_text(k(14), bot - sh / 2, text=sub, anchor="w", fill=SECONDARY, font=ui.fonts["label"])
        dt = delta_text(metric, cur, base, d) if ok else ""
        if dt:
            c.create_text(w - k(14), k(18), text=dt, anchor="e", fill=col, font=ui.fonts["chip"])
        c.bind("<Button-1>", lambda e: toggle_breakdown(speech, rows, metric))
        return c

    breakdown = {"frame": None}

    def toggle_breakdown(speech, rows, metric):
        if breakdown["frame"]:
            breakdown["frame"].destroy()
        if st["open"] == metric:
            st["open"] = breakdown["frame"] = None
            return
        st["open"] = metric
        p = Panel(body, ui, f"{METRICS[metric][0]} by app"); breakdown["frame"] = p
        p.pack(fill="x", pady=(0, k(12)), before=summary_anchor[0])
        try:
            data = speech.by_app(rows, metric)
            items = list(data)
        except (TypeError, ValueError):
            items = []
        if not items:
            _label(p.rows, ui, "Not enough dictations per app yet.", "small", SECONDARY, FIELD).pack(anchor="w")
        for app, v, count in items:
            r = tk.Frame(p.rows, bg=FIELD); r.pack(fill="x", pady=k(2))
            _label(r, ui, f"{app}  \u00b7  {count} dictations", "body", NAME, FIELD).pack(side="left")
            _label(r, ui, f"{fmt(v, metric)} {METRICS[metric][1]}", "body", TEXT, FIELD).pack(side="right")

    summary_anchor = [None]

    def ready_view(speech, rows):
        order = sorted(METRICS, key=lambda m: (m not in goals(), list(METRICS).index(m)))
        grid = tk.Frame(body, bg=FILL); grid.pack(fill="x", pady=(0, k(12)))
        for i, m in enumerate(order):
            tile(grid, speech, rows, m, m in goals()).grid(row=i // 3, column=i % 3, padx=(0, k(10)), pady=(0, k(10)), sticky="nw")
        sp = Panel(body, ui, "Summary"); sp.pack(fill="x", pady=(0, k(12))); summary_anchor[0] = sp
        try:
            lines = list(speech.summary(rows, goals()))
        except (TypeError, ValueError):
            lines = []
        for line in lines or ["Nothing notable yet."]:
            _label(sp.rows, ui, "•  " + line, "body", NAME, FIELD).pack(anchor="w", pady=k(2))

    def render_all():
        clear()
        breakdown["frame"] = st["open"] = None
        try:
            from . import speech
        except ImportError:
            p = Panel(body, ui, "Speech analysis"); p.pack(fill="x")
            _label(p.rows, ui, "Not available in this build yet.", "body", SECONDARY, FIELD).pack(anchor="w")
            return
        rows = st["rows"]
        n, needed = speech.ready(rows)
        if n < needed:
            collecting(n, needed)
        else:
            ready_view(speech, rows_for(rows, st["app"]))

    def load():
        c = store.merged_config()
        coach = c.get("coach", {})
        for m, chip in chips.items():
            chip.on = m in (coach.get("goals") or []); chip.draw()
        hint.set(coach.get("card_hint", True))
        st["rows"] = store.read_history()
        apps = sorted({r.get("app") for r in st["rows"] if r.get("app")})
        app_sel.reset(["All apps"] + apps)
        render_all()
    def reset():
        from . import speech
        store.strip_history_keys(speech.FIELDS); load()  # dictations stay; the metrics and their baseline start over
    _armed_pill(pg.bar, ui, "Reset speech data", "Click again to drop every speaking metric", reset).pack(side="right")
    return pg, load
