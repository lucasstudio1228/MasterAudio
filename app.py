"""Giao diện desktop cho Tool Tempo 4.5× & Master Audio."""
from __future__ import annotations

import os
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from audio_engine import (AUDIO_EXTS, PRESETS, AudioToolError, collect_audio,
                          export_tempo, ffmpeg_version, find_ffmpeg, master_audio,
                          normalize_factor)

TOOL_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = TOOL_DIR / "output"


class AudioToolApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Tempo 4.5× & Master Audio")
        self.geometry("980x720")
        self.minsize(820, 620)
        self.files: list[Path] = []
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.output_var = tk.StringVar(value=str(DEFAULT_OUTPUT))
        self.tempo_var = tk.StringVar(value="4.5")
        self.preset_var = tk.StringVar(value="natural")
        self.format_var = tk.StringVar(value="wav")
        self.status_var = tk.StringVar(value="Sẵn sàng")
        self._build()
        self.after(100, self._poll)
        self.after(200, self._check_ffmpeg)

    def _build(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="Tempo 4.5× & Master Audio",
                  font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(root, text="Tool độc lập · xử lý hàng loạt · không ghi đè file gốc",
                  foreground="#555").pack(anchor="w", pady=(2, 14))

        file_box = ttk.LabelFrame(root, text="1. Chọn file audio", padding=10)
        file_box.pack(fill="both", expand=True)
        controls = ttk.Frame(file_box)
        controls.pack(fill="x", pady=(0, 8))
        ttk.Button(controls, text="＋ Thêm file", command=self._add_files).pack(side="left", padx=(0, 6))
        ttk.Button(controls, text="＋ Quét thư mục", command=self._add_folder).pack(side="left", padx=6)
        ttk.Button(controls, text="− Bỏ mục chọn", command=self._remove_selected).pack(side="left", padx=6)
        ttk.Button(controls, text="Xóa danh sách", command=self._clear).pack(side="left", padx=6)
        self.count_label = ttk.Label(controls, text="0 file")
        self.count_label.pack(side="right")
        self.listbox = tk.Listbox(file_box, selectmode="extended", height=11,
                                  font=("Consolas", 9), activestyle="none")
        self.listbox.pack(fill="both", expand=True)

        out = ttk.LabelFrame(root, text="2. Thư mục kết quả", padding=10)
        out.pack(fill="x", pady=10)
        ttk.Entry(out, textvariable=self.output_var).pack(side="left", fill="x", expand=True)
        ttk.Button(out, text="Chọn…", command=self._choose_output).pack(side="left", padx=(8, 0))
        ttk.Button(out, text="Mở folder", command=self._open_output).pack(side="left", padx=(8, 0))

        settings = ttk.LabelFrame(root, text="3. Chọn cách xử lý", padding=10)
        settings.pack(fill="x")
        ttk.Label(settings, text="Tempo:").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(settings, from_=0.2, to=5.0, increment=0.1, width=8,
                    textvariable=self.tempo_var).grid(row=0, column=1, sticky="w", padx=(6, 22))
        ttk.Label(settings, text="× (mặc định 4.5×)").grid(row=0, column=2, sticky="w")
        ttk.Label(settings, text="Preset master:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        preset = ttk.Combobox(settings, state="readonly", width=31, textvariable=self.preset_var,
                              values=list(PRESETS))
        preset.grid(row=1, column=1, columnspan=2, sticky="w", padx=(6, 22), pady=(8, 0))
        ttk.Label(settings, text="natural = -14 LUFS · dynamic = -16 · loud = -10",
                  foreground="#555").grid(row=1, column=3, sticky="w", pady=(8, 0))
        ttk.Label(settings, text="Định dạng:").grid(row=0, column=3, sticky="e", padx=(30, 6))
        ttk.Combobox(settings, state="readonly", width=12, textvariable=self.format_var,
                     values=["wav", "mp3", "both"]).grid(row=0, column=4, sticky="w")

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=12)
        self.tempo_btn = ttk.Button(actions, text="⚡ Tạo tempo", command=lambda: self._start("tempo"))
        self.tempo_btn.pack(side="left", padx=(0, 8))
        self.master_btn = ttk.Button(actions, text="🎚 Master audio", command=lambda: self._start("master"))
        self.master_btn.pack(side="left", padx=8)
        self.both_btn = ttk.Button(actions, text="⚡ Tempo → 🎚 Master",
                                   command=lambda: self._start("both"))
        self.both_btn.pack(side="left", padx=8)
        self.stop_btn = ttk.Button(actions, text="⏹ Dừng", command=self._stop, state="disabled")
        self.stop_btn.pack(side="right")

        self.progress = ttk.Progressbar(root, mode="determinate")
        self.progress.pack(fill="x")
        ttk.Label(root, textvariable=self.status_var).pack(anchor="w", pady=(4, 5))
        log_frame = ttk.LabelFrame(root, text="Nhật ký", padding=6)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, height=9, wrap="word", state="disabled",
                           font=("Consolas", 9), background="#101218", foreground="#e5e7eb")
        self.log.pack(fill="both", expand=True)

    def _add_files(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in sorted(AUDIO_EXTS))
        chosen = filedialog.askopenfilenames(title="Chọn file audio",
                                             filetypes=[("Audio", patterns), ("Tất cả", "*.*")])
        self._append_paths(Path(p) for p in chosen)

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory(title="Chọn thư mục chứa audio")
        if folder:
            self._append_paths(collect_audio(Path(folder), recursive=True))

    def _append_paths(self, paths) -> None:
        known = {str(p.resolve()).casefold() for p in self.files}
        for path in paths:
            if path.is_file() and path.suffix.lower() in AUDIO_EXTS:
                key = str(path.resolve()).casefold()
                if key not in known:
                    self.files.append(path.resolve())
                    known.add(key)
        self.files.sort(key=lambda p: str(p).casefold())
        self._refresh_files()

    def _refresh_files(self) -> None:
        self.listbox.delete(0, "end")
        for path in self.files:
            self.listbox.insert("end", str(path))
        self.count_label.config(text=f"{len(self.files)} file")

    def _remove_selected(self) -> None:
        selected = set(self.listbox.curselection())
        self.files = [path for i, path in enumerate(self.files) if i not in selected]
        self._refresh_files()

    def _clear(self) -> None:
        self.files.clear()
        self._refresh_files()

    def _choose_output(self) -> None:
        folder = filedialog.askdirectory(title="Chọn thư mục lưu kết quả")
        if folder:
            self.output_var.set(folder)

    def _open_output(self) -> None:
        folder = Path(self.output_var.get().strip() or DEFAULT_OUTPUT)
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def _check_ffmpeg(self) -> None:
        try:
            exe = find_ffmpeg(TOOL_DIR)
            self._write_log(f"✓ {ffmpeg_version(exe)}")
        except Exception as exc:
            self._write_log(f"⚠ {exc}")

    def _start(self, mode: str) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.files:
            messagebox.showwarning("Chưa có file", "Hãy thêm ít nhất một file audio.")
            return
        try:
            factor = normalize_factor(self.tempo_var.get())
            output = Path(self.output_var.get().strip() or DEFAULT_OUTPUT).resolve()
            exe = find_ffmpeg(TOOL_DIR)
        except Exception as exc:
            messagebox.showerror("Không thể chạy", str(exc))
            return
        self.cancel.clear()
        self.progress.config(maximum=len(self.files), value=0)
        self._set_busy(True)
        args = (mode, list(self.files), output, factor, self.preset_var.get(),
                self.format_var.get(), exe)
        self.worker = threading.Thread(target=self._work, args=args, daemon=True)
        self.worker.start()

    def _work(self, mode: str, files: list[Path], output: Path, factor: float,
              preset: str, fmt: str, exe: str) -> None:
        ok = failed = 0
        self.events.put(("log", f"\nBắt đầu {mode}: {len(files)} file → {output}"))
        for index, source in enumerate(files, 1):
            if self.cancel.is_set():
                break
            self.events.put(("status", f"Đang xử lý {index}/{len(files)}: {source.name}"))
            try:
                target = source
                if mode in {"tempo", "both"}:
                    target = export_tempo(source, output, factor, exe,
                                          log=lambda m: self.events.put(("log", m)),
                                          cancel=self.cancel)
                if mode in {"master", "both"}:
                    master_audio(target, output, preset, fmt, exe,
                                 log=lambda m: self.events.put(("log", m)),
                                 cancel=self.cancel)
                ok += 1
            except Exception as exc:
                failed += 1
                self.events.put(("log", f"✗ {source.name}: {exc}"))
            self.events.put(("progress", index))
        stopped = self.cancel.is_set()
        self.events.put(("done", (ok, failed, stopped, output)))

    def _stop(self) -> None:
        self.cancel.set()
        self.status_var.set("Đang dừng ffmpeg an toàn…")

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for button in (self.tempo_btn, self.master_btn, self.both_btn):
            button.config(state=state)
        self.stop_btn.config(state="normal" if busy else "disabled")

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._write_log(str(payload))
                elif kind == "status":
                    self.status_var.set(str(payload))
                elif kind == "progress":
                    self.progress.config(value=int(payload))
                elif kind == "done":
                    ok, failed, stopped, output = payload
                    self._set_busy(False)
                    self.status_var.set(
                        f"{'Đã dừng' if stopped else 'Hoàn tất'} · {ok} thành công · {failed} lỗi")
                    self._write_log(f"{'ĐÃ DỪNG' if stopped else 'HOÀN TẤT'}: "
                                    f"{ok} thành công, {failed} lỗi. Kết quả: {output}")
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _write_log(self, text: str) -> None:
        self.log.config(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.config(state="disabled")


if __name__ == "__main__":
    AudioToolApp().mainloop()
