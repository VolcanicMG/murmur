"""Hardware-bound settings controls: input-device picker with a quick test, and a hotkey recorder."""
import queue, threading, tkinter as tk
from . import store
from .inputs import Select, TextField
from .widgets import Pill


DEFAULT_MIC = "System default"


def input_devices():
    """Input device labels "<name> (<hostapi>)", de-duplicated, as [(label, name)]; sounddevice imported lazily (Linux tests)."""
    import sounddevice as sd
    apis, seen, out = sd.query_hostapis(), set(), []
    for d in sd.query_devices():
        label = f"{d['name']} ({apis[d['hostapi']]['name']})"
        if d["max_input_channels"] > 0 and label not in seen:
            seen.add(label); out.append((label, d["name"]))
    return out


class MicField(tk.Frame):
    """Input-device Select plus Test mic (1.5 s on a worker thread, peak level to the status line). Stores the device NAME."""

    def __init__(self, parent, ui, status):
        super().__init__(parent, bg=parent.cget("bg"))
        self.status, self.q, self.names = status, queue.Queue(), {DEFAULT_MIC: ""}
        try:
            devs = input_devices()
        except Exception:
            devs = []
        self.names.update(devs)
        self.sel = Select(self, ui, list(self.names)); self.sel.pack(side="left")
        Pill(self, ui, "Test mic", self.test).pack(side="left", padx=(ui.k(8), 0))

    def get(self):
        return self.names.get(self.sel.get(), "")

    def set(self, name):
        label = next((l for l, n in self.names.items() if n == name), None)
        if label is None and name:  # configured device not present now: keep it selectable so a save doesn't drop it
            label = f"{name} (not found)"; self.names[label] = name; self.sel.reset(list(self.names))
        self.sel.set(label or DEFAULT_MIC)

    def test(self):
        self.status.set("Listening for 1.5 s\u2026", "info")
        threading.Thread(target=self._rec, args=(self.get(),), daemon=True).start()
        self.after(100, self._poll)

    def _rec(self, name):
        try:
            import sounddevice as sd
            idx = store.resolve_device(name, sd.query_devices())
            data = sd.rec(int(1.5 * 16000), samplerate=16000, channels=1, dtype="float32", device=idx, blocking=True)
            self.q.put(float(abs(data).max()))
        except Exception as e:
            self.q.put(e)

    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            res = self.q.get_nowait()
        except queue.Empty:
            self.after(100, self._poll); return
        if isinstance(res, Exception):
            self.status.set(f"Can't open the input: {res}", "error")
        elif res < 0.01:
            self.status.set("No signal \u2014 check the input", "error")
        else:
            self.status.set(f"Mic ok \u00b7 peak {res:.2f}", "success")


class HotkeyField(tk.Frame):
    """Entry plus a Record pill: a worker thread runs keyboard.read_hotkey, the Tk side polls a queue (Esc cancels)."""

    def __init__(self, parent, ui, status):
        super().__init__(parent, bg=parent.cget("bg"))
        self.status, self.q, self.busy = status, queue.Queue(), False
        self.entry = TextField(self, ui, 22)
        self.entry.pack(side="left"); Pill(self, ui, "Record", self.record).pack(side="left", padx=(ui.k(8), 0))

    def get(self):
        return self.entry.get()

    def set(self, v):
        self.entry.set(v)

    def record(self):
        if self.busy:
            return
        self.busy = True
        self.status.set("Press the keys… (Esc cancels)", "info")
        threading.Thread(target=self._read, daemon=True).start()
        self.after(100, self._poll)

    def _read(self):
        from .app import S
        S["capturing"] = True  # the tray's own hotkeys must ignore what is being recorded
        try:
            import keyboard
            name = keyboard.read_hotkey(suppress=False)
            self.q.put(name if "+" in name else keyboard.normalize_name(name))
        except Exception as e:
            self.q.put(e)
        finally:
            S["capturing"] = False

    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            res = self.q.get_nowait()
        except queue.Empty:
            self.after(100, self._poll); return
        self.busy = False
        if isinstance(res, Exception):
            self.status.set(f"Can't record keys: {res}", "error")
        elif res == "esc":
            self.status.set("Recording cancelled.", "info")
        else:
            self.set(res); self.status.set(f"Captured {res}.", "info")
