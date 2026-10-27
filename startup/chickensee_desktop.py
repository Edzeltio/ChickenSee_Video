import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

try:
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ChickenSee.ControlCenter.App")
except Exception:
    pass

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

import psutil

if __package__:
    from .configuration import (
        AGENT_DIR,
        DEFAULT_STORAGE_ROOT,
        PROJECT_ROOT,
        read_agent_settings,
        validate_agent_settings,
        write_agent_settings,
        write_watch_directories,
    )
else:
    from configuration import (
        AGENT_DIR,
        DEFAULT_STORAGE_ROOT,
        PROJECT_ROOT,
        read_agent_settings,
        validate_agent_settings,
        write_agent_settings,
        write_watch_directories,
    )


HEALTH_URL = "http://127.0.0.1:8000/health"
QUEUE_URL = "http://127.0.0.1:8000/queue"
REFRESH_INTERVAL_MS = 2000
POWERSHELL = "powershell.exe"

COLORS = {
    "background":     "#0d0f17",         # Deep midnight background
    "surface":        "#151824",         # Rich dark card surface
    "surface_alt":    "#1a1e2e",         # Secondary/table alternate surface
    "surface_raised": "#202538",         # Elevated cards, inputs, dropdowns
    "ink":            "#f1f5f9",         # High contrast crisp white
    "ink_dim":        "#94a3b8",         # Secondary slate
    "muted":          "#56627a",         # Muted labels/placeholders
    "primary":        "#6366f1",         # Modern Indigo/Electric Violet (replacing harsh orange)
    "primary_dark":   "#4f46e5",         # Pressed indigo
    "primary_hover":  "#818cf8",         # Hover soft indigo
    "primary_soft":   "#1e1f3d",         # Subtle tinted indigo background
    "primary_glow":   "#312e81",         # Focus and active borders
    "green":          "#10b981",         # Emerald green
    "green_hover":    "#059669",
    "green_soft":     "#062e20",         # Tinted emerald badge background
    "green_text":     "#34d399",         # High-legibility emerald text
    "red":            "#ef4444",         # Coral red
    "red_hover":      "#dc2626",
    "red_soft":       "#2d1014",         # Tinted red badge background
    "red_text":       "#f87171",         # High-legibility red text
    "amber":          "#f59e0b",         # Amber/gold
    "amber_soft":     "#2b1f07",         # Tinted amber badge background
    "amber_text":     "#fbbf24",         # High-legibility amber text
    "border":         "#23293d",         # Clean subtle structural border
    "border_active":  "#6366f1",         # Active border
    "header_bg":      "#090b12",         # Sleek dark header
    "soft_blue":      "#1e1f3d",         # Backward-compatibility alias
}

SUPERVISOR_SCRIPT = Path("startup") / "run-chickensee.ps1"


def find_service_processes() -> Dict[str, List[int]]:
    matches: Dict[str, List[int]] = {
        "sync-agent": [],
        "analytics-api": [],
        "analytics-worker": [],
        "supervisor": [],
    }
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (process.info["name"] or "").lower()
            command = " ".join(process.info["cmdline"] or []).lower()
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue

        if name in ("node.exe", "node") and "watcher.js" in command:
            matches["sync-agent"].append(process.info["pid"])
        elif name.startswith("python") and "-m analytics.api" in command:
            matches["analytics-api"].append(process.info["pid"])
        elif name.startswith("python") and "-m analytics.worker" in command:
            matches["analytics-worker"].append(process.info["pid"])
        elif "run-chickensee.ps1" in command:
            matches["supervisor"].append(process.info["pid"])
    return matches


def start_supervisor(
    project_root: Path = PROJECT_ROOT,
    storage_root: Path = DEFAULT_STORAGE_ROOT,
) -> subprocess.Popen:
    script_path = project_root / SUPERVISOR_SCRIPT
    if not script_path.is_file():
        raise FileNotFoundError(f"ChickenSee startup supervisor not found: {script_path}")
    log_dir = storage_root.expanduser() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    launch_log = log_dir / "startup-launch.log"
    with launch_log.open("a", encoding="utf-8") as output:
        return subprocess.Popen(
            [
                POWERSHELL,
                "-NoProfile",
                "-WindowStyle",
                "Hidden",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
            ],
            cwd=project_root,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def capture_service_processes() -> Dict[str, List[Tuple[psutil.Process, float]]]:
    captured: Dict[str, List[Tuple[psutil.Process, float]]] = {
        "sync-agent": [],
        "analytics-api": [],
        "analytics-worker": [],
        "supervisor": [],
    }
    for category, pids in find_service_processes().items():
        for pid in pids:
            try:
                process = psutil.Process(pid)
                captured[category].append((process, process.create_time()))
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
    return captured


def stop_processes_after_signal(
    targets: Dict[str, List[Tuple[psutil.Process, float]]],
    graceful_timeout_s: float = 15.0,
) -> bool:
    """Wait for the supervisor, then stop only captured ChickenSee processes if needed."""
    all_targets = [target for group in targets.values() for target in group]
    deadline = time.monotonic() + graceful_timeout_s

    def still_running(target: Tuple[psutil.Process, float]) -> bool:
        process, created_at = target
        try:
            return process.is_running() and process.create_time() == created_at
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            return False

    while time.monotonic() < deadline and any(still_running(target) for target in all_targets):
        time.sleep(0.25)

    remaining_supervisors = [
        target for target in targets["supervisor"] if still_running(target)
    ]
    remaining_services = [
        target
        for name, group in targets.items()
        if name != "supervisor"
        for target in group
        if still_running(target)
    ]
    if not remaining_supervisors and not remaining_services:
        return False

    for process, _ in remaining_supervisors:
        try:
            process.terminate()
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    if remaining_supervisors:
        psutil.wait_procs([target[0] for target in remaining_supervisors], timeout=5)
    still_running_supervisors = [
        process.pid
        for process, created_at in remaining_supervisors
        if still_running((process, created_at))
    ]
    if still_running_supervisors:
        raise RuntimeError(
            "Could not stop the ChickenSee supervisor: "
            + ", ".join(f"PID {pid}" for pid in still_running_supervisors)
        )

    for process, _ in remaining_services:
        try:
            process.terminate()
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    _, stubborn = psutil.wait_procs(
        [target[0] for target in remaining_services], timeout=5
    )
    for process in stubborn:
        try:
            process.kill()
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    if stubborn:
        _, still_stubborn = psutil.wait_procs(stubborn, timeout=3)
        if still_stubborn:
            raise RuntimeError(
                "Could not stop ChickenSee processes: "
                + ", ".join(f"PID {process.pid}" for process in still_stubborn)
            )
    still_running_services = [
        process.pid
        for process, created_at in remaining_services
        if still_running((process, created_at))
    ]
    if still_running_services:
        raise RuntimeError(
            "ChickenSee services are still running: "
            + ", ".join(f"PID {pid}" for pid in still_running_services)
        )
    return True


def request_stop(
    storage_root: Path,
    supervisor_pids: List[int],
    agent_dir: Path = AGENT_DIR,
) -> None:
    timestamp = datetime.now().isoformat()
    storage_roots = {storage_root}
    supervisor_state = agent_dir / ".supervisor-state.json"
    if supervisor_state.is_file():
        state = json.loads(supervisor_state.read_text(encoding="utf-8-sig"))
        if state.get("process_id") in supervisor_pids:
            storage_roots.add(Path(state["storage_root"]))
    stop_files = [agent_dir / ".stop-request"]
    stop_files.extend(root / "logs" / "stop-request" for root in storage_roots)
    for stop_file in stop_files:
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        stop_file.write_text(timestamp, encoding="utf-8")


def format_queue_progress(status: str, progress_percent: Optional[float]) -> str:
    if progress_percent is None:
        return "Waiting" if status in ("QUEUED", "WAITING") else "—"
    return f"{max(0.0, min(float(progress_percent), 100.0)):.1f}%"


class ChickenSeeDesktop:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ChickenSee Control Center")
        self.root.geometry("1050x760")
        self.root.minsize(900, 650)
        self.root.configure(bg=COLORS["background"])
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
        self._apply_window_icon()
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.refresh_in_progress = False
        self.refresh_pending = False
        self.active_job_id: Optional[int] = None
        self.cancel_in_progress = False
        self.starting_services = False
        self.stopping_services = False
        self.settings = read_agent_settings()
        self._configure_styles()
        self._build_layout()
        self._load_settings_into_form()
        self._refresh_status()
        self.root.after(250, self._drain_events)
        self.root.after(REFRESH_INTERVAL_MS, self._schedule_refresh)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")

        # Configure Combobox popdown listbox via option database for dark mode
        self.root.option_add("*TCombobox*Listbox.background", COLORS["surface_raised"])
        self.root.option_add("*TCombobox*Listbox.foreground", COLORS["ink"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", COLORS["primary_soft"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", COLORS["primary_hover"])
        self.root.option_add("*TCombobox*Listbox.relief", "flat")
        self.root.option_add("*TCombobox*Listbox.borderWidth", "0")

        style.configure("TFrame", background=COLORS["background"])
        style.configure("Surface.TFrame", background=COLORS["surface"])
        style.configure(
            "TLabel",
            background=COLORS["background"],
            foreground=COLORS["ink"],
            font=("Segoe UI", 10),
        )
        style.configure(
            "Title.TLabel",
            background=COLORS["header_bg"],
            foreground=COLORS["ink"],
            font=("Segoe UI Semibold", 23),
        )
        style.configure(
            "Subtitle.TLabel",
            background=COLORS["background"],
            foreground=COLORS["ink_dim"],
            font=("Segoe UI", 10),
        )
        style.configure(
            "Card.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["ink"],
            font=("Segoe UI", 10),
        )
        style.configure(
            "TButton",
            padding=(14, 8),
            font=("Segoe UI Semibold", 10),
            borderwidth=0,
            focuscolor="none",
            background=COLORS["surface_raised"],
            foreground=COLORS["ink"],
        )
        style.map(
            "TButton",
            background=[("active", COLORS["border"])],
            foreground=[("active", COLORS["ink"])],
        )
        style.configure(
            "Primary.TButton",
            background=COLORS["primary"],
            foreground="#ffffff",
            padding=(16, 9),
            font=("Segoe UI Semibold", 10),
            focuscolor="none",
        )
        style.map(
            "Primary.TButton",
            background=[("active", COLORS["primary_hover"]), ("disabled", COLORS["border"])],
            foreground=[("disabled", COLORS["muted"]), ("active", "#ffffff")],
        )
        style.configure(
            "Secondary.TButton",
            background=COLORS["surface_raised"],
            foreground=COLORS["ink"],
            bordercolor=COLORS["border"],
            borderwidth=1,
            relief="solid",
            padding=(14, 7),
            font=("Segoe UI Semibold", 9),
            focuscolor="none",
        )
        style.map("Secondary.TButton", background=[("active", COLORS["border"])])
        style.configure(
            "Cancel.TButton",
            background=COLORS["red_soft"],
            foreground=COLORS["red_text"],
            bordercolor=COLORS["red"],
            borderwidth=1,
            relief="solid",
            padding=(12, 6),
            font=("Segoe UI Semibold", 9),
            focuscolor="none",
        )
        style.map(
            "Cancel.TButton",
            background=[("active", COLORS["red_hover"]), ("disabled", COLORS["surface"])],
            foreground=[("active", "#ffffff"), ("disabled", COLORS["muted"])],
        )
        style.configure(
            "Stop.TButton",
            background=COLORS["red"],
            foreground="#ffffff",
            padding=(16, 10),
            font=("Segoe UI Semibold", 10),
            focuscolor="none",
        )
        style.map(
            "Stop.TButton",
            background=[
                ("disabled", COLORS["border"]),
                ("active", COLORS["red_hover"]),
            ],
            foreground=[("disabled", COLORS["muted"]), ("active", "#ffffff")],
        )
        style.configure(
            "Start.TButton",
            background=COLORS["green"],
            foreground="#ffffff",
            padding=(16, 10),
            font=("Segoe UI Semibold", 10),
            focuscolor="none",
        )
        style.map(
            "Start.TButton",
            background=[
                ("disabled", COLORS["border"]),
                ("active", COLORS["green_hover"]),
            ],
            foreground=[("disabled", COLORS["muted"]), ("active", "#ffffff")],
        )
        style.configure("TNotebook", background=COLORS["background"], borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            padding=(22, 11),
            background=COLORS["surface"],
            foreground=COLORS["ink_dim"],
            font=("Segoe UI Semibold", 10),
            focuscolor="none",
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", COLORS["background"])],
            foreground=[("selected", COLORS["primary"])],
        )
        style.configure(
            "TLabelframe",
            background=COLORS["surface"],
            bordercolor=COLORS["border"],
            relief="solid",
            borderwidth=1,
        )
        style.configure(
            "TLabelframe.Label",
            background=COLORS["surface"],
            foreground=COLORS["ink_dim"],
            font=("Segoe UI Semibold", 10),
        )
        style.configure(
            "Treeview",
            background=COLORS["surface"],
            fieldbackground=COLORS["surface"],
            foreground=COLORS["ink"],
            bordercolor=COLORS["border"],
            borderwidth=0,
            rowheight=36,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Treeview.Heading",
            background=COLORS["surface_raised"],
            foreground=COLORS["ink_dim"],
            font=("Segoe UI Semibold", 9),
            relief="flat",
            borderwidth=0,
            padding=(12, 10),
        )
        style.map("Treeview.Heading", background=[("active", COLORS["border"])])
        style.map(
            "Treeview",
            background=[("selected", COLORS["primary_soft"])],
            foreground=[("selected", COLORS["primary_hover"])],
        )
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor=COLORS["surface_raised"],
            background=COLORS["primary"],
            bordercolor=COLORS["border"],
            lightcolor=COLORS["primary"],
            darkcolor=COLORS["primary_dark"],
            relief="flat",
            borderwidth=0,
            thickness=6,
        )
        style.configure(
            "TEntry",
            fieldbackground=COLORS["surface_raised"],
            foreground=COLORS["ink"],
            insertcolor=COLORS["ink"],
            bordercolor=COLORS["border"],
            selectbackground=COLORS["primary_soft"],
            selectforeground=COLORS["primary_hover"],
        )
        style.configure(
            "TCombobox",
            fieldbackground=COLORS["surface_raised"],
            foreground=COLORS["ink"],
            background=COLORS["surface_raised"],
            selectbackground=COLORS["primary_soft"],
            selectforeground=COLORS["primary_hover"],
            arrowcolor=COLORS["primary_hover"],
        )
        style.configure(
            "TScrollbar",
            background=COLORS["surface_raised"],
            troughcolor=COLORS["surface"],
            arrowcolor=COLORS["ink_dim"],
            borderwidth=0,
            relief="flat",
        )
        style.map("TScrollbar", background=[("active", COLORS["border"])])
        style.configure(
            "TRadiobutton",
            background=COLORS["background"],
            foreground=COLORS["ink_dim"],
            font=("Segoe UI", 9),
            focuscolor="none",
        )
        style.map(
            "TRadiobutton",
            foreground=[("selected", COLORS["primary"]), ("active", COLORS["ink"])],
        )
        style.configure(
            "TCheckbutton",
            background=COLORS["surface"],
            foreground=COLORS["ink"],
            font=("Segoe UI", 10),
            focuscolor="none",
        )

    def _apply_window_icon(self) -> None:
        icon_ico = PROJECT_ROOT / "favicon.ico"
        icon_png = Path(__file__).resolve().parent / "favicon_logo128.png"
        if not icon_ico.is_file():
            icon_ico = Path(__file__).resolve().parent / "favicon.ico"
        if not icon_png.is_file():
            icon_png = PROJECT_ROOT / "favicon.png"

        if icon_ico.is_file():
            try:
                self.root.iconbitmap(default=str(icon_ico))
            except Exception:
                try:
                    self.root.iconbitmap(str(icon_ico))
                except Exception:
                    pass

        if icon_png.is_file():
            try:
                if Image and ImageTk:
                    pil_img = Image.open(icon_png).resize((32, 32), Image.Resampling.LANCZOS)
                    self._icon_photo = ImageTk.PhotoImage(pil_img)
                    self.root.iconphoto(True, self._icon_photo)
                else:
                    self._icon_photo = tk.PhotoImage(file=str(icon_png))
                    self.root.iconphoto(True, self._icon_photo)
            except Exception:
                pass

    def _build_layout(self) -> None:
        # Dark header strip
        header = tk.Frame(
            self.root,
            bg=COLORS["header_bg"],
            padx=24,
            pady=16,
        )
        header.pack(fill="x")
        heading = tk.Frame(header, bg=COLORS["header_bg"])
        heading.pack(side="left", fill="x", expand=True)

        brand_row = tk.Frame(heading, bg=COLORS["header_bg"])
        brand_row.pack(side="left", fill="x")

        self._header_logo_photo = None
        logo_candidates = [
            Path(__file__).resolve().parent / "favicon_badge.png",
            Path(__file__).resolve().parent / "favicon_logo256.png",
            PROJECT_ROOT / "favicon.png",
        ]
        logo_path = next((p for p in logo_candidates if p.is_file()), None)
        if logo_path and Image and ImageTk:
            try:
                logo_raw = Image.open(logo_path).convert("RGBA")
                logo_img = logo_raw.resize((48, 48), Image.Resampling.LANCZOS)
                self._header_logo_photo = ImageTk.PhotoImage(logo_img)
                logo_label = tk.Label(
                    brand_row,
                    image=self._header_logo_photo,
                    bg=COLORS["header_bg"],
                    bd=0,
                )
                logo_label.pack(side="left", padx=(0, 16))
            except Exception:
                pass

        titles = tk.Frame(brand_row, bg=COLORS["header_bg"])
        titles.pack(side="left")
        tk.Label(
            titles,
            text="ChickenSee",
            bg=COLORS["header_bg"],
            fg=COLORS["ink"],
            font=("Segoe UI Semibold", 22),
        ).pack(anchor="w")
        tk.Label(
            titles,
            text="Video analysis and sync at a glance",
            bg=COLORS["header_bg"],
            fg=COLORS["ink_dim"],
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(3, 0))
        self.header_status = tk.Label(
            header,
            text="● Checking system…",
            bg=COLORS["primary_soft"],
            fg=COLORS["primary"],
            font=("Segoe UI Semibold", 9),
            padx=14,
            pady=8,
        )
        self.header_status.pack(side="right", anchor="center")

        # Custom Segmented Navigation Bar
        nav_container = tk.Frame(self.root, bg=COLORS["header_bg"], padx=24)
        nav_container.pack(fill="x")

        self.tab_buttons: Dict[str, tk.Label] = {}
        self.tab_indicators: Dict[str, tk.Frame] = {}
        self.pages: Dict[str, tk.Frame] = {}
        self.active_tab = "dashboard"

        tab_items = [
            ("dashboard", "Dashboard", "📊"),
            ("queue", "Queue", "⚡"),
            ("setup", "Setup & Settings", "⚙"),
        ]

        for tab_id, label_text, icon in tab_items:
            tab_frame = tk.Frame(nav_container, bg=COLORS["header_bg"], cursor="hand2")
            tab_frame.pack(side="left", padx=(0, 4))

            tab_btn = tk.Label(
                tab_frame,
                text=f"{icon}  {label_text}",
                bg=COLORS["header_bg"],
                fg=COLORS["ink_dim"],
                font=("Segoe UI Semibold", 10),
                padx=18,
                pady=11,
                cursor="hand2",
            )
            tab_btn.pack(side="top")

            indicator = tk.Frame(tab_frame, bg=COLORS["header_bg"], height=3)
            indicator.pack(side="bottom", fill="x")

            self.tab_buttons[tab_id] = tab_btn
            self.tab_indicators[tab_id] = indicator

            for w in (tab_frame, tab_btn, indicator):
                w.bind("<Button-1>", lambda e, tid=tab_id: self._switch_tab(tid))
                w.bind("<Enter>", lambda e, tid=tab_id: self._on_tab_hover(tid, True))
                w.bind("<Leave>", lambda e, tid=tab_id: self._on_tab_hover(tid, False))

        # Thin divider below navigation
        nav_divider = tk.Frame(self.root, bg=COLORS["border"], height=1)
        nav_divider.pack(fill="x")

        # Page Container
        self.page_container = tk.Frame(self.root, bg=COLORS["background"], padx=24, pady=20)
        self.page_container.pack(fill="both", expand=True)

        self.dashboard = tk.Frame(self.page_container, bg=COLORS["background"])
        self.queue_page = tk.Frame(self.page_container, bg=COLORS["background"])
        self.setup = tk.Frame(self.page_container, bg=COLORS["background"])

        self.pages["dashboard"] = self.dashboard
        self.pages["queue"] = self.queue_page
        self.pages["setup"] = self.setup

        # Notebook compatibility object
        class _TabsCompat:
            def __init__(self, parent):
                self.parent = parent
            def select(self, target=None):
                if target is None:
                    return self.parent.pages.get(self.parent.active_tab)
                for tid, page in self.parent.pages.items():
                    if target in (tid, page):
                        self.parent._switch_tab(tid)
                        return
        self.tabs = _TabsCompat(self)

        self._build_dashboard()
        self._build_queue()
        self._build_setup()

        # Activate initial tab
        self._switch_tab("dashboard")

    def _switch_tab(self, tab_id: str) -> None:
        self.active_tab = tab_id
        for tid, page in self.pages.items():
            if tid == tab_id:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()

        for tid, btn in self.tab_buttons.items():
            indicator = self.tab_indicators[tid]
            if tid == tab_id:
                btn.configure(fg=COLORS["ink"])
                indicator.configure(bg=COLORS["primary"])
            else:
                btn.configure(fg=COLORS["ink_dim"])
                indicator.configure(bg=COLORS["header_bg"])

    def _on_tab_hover(self, tab_id: str, is_hover: bool) -> None:
        if tab_id == self.active_tab:
            return
        btn = self.tab_buttons[tab_id]
        if is_hover:
            btn.configure(fg=COLORS["ink"])
        else:
            btn.configure(fg=COLORS["ink_dim"])


    def _build_dashboard(self) -> None:
        actions = ttk.Frame(self.dashboard)
        actions.pack(fill="x", pady=(0, 18))
        self.start_button = ttk.Button(
            actions,
            text="▶  Start ChickenSee",
            style="Start.TButton",
            command=self.start_services,
        )
        self.start_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(
            actions,
            text="⏻  Stop ChickenSee",
            style="Stop.TButton",
            command=self.stop_services,
        )
        self.stop_button.pack(side="left")
        ttk.Button(actions, text="Open API docs", command=self.open_api_docs).pack(
            side="right"
        )
        ttk.Button(actions, text="Open logs", command=self.open_logs).pack(
            side="right", padx=(0, 8)
        )

        cards = ttk.Frame(self.dashboard)
        cards.pack(fill="x")
        self.service_labels: Dict[str, tk.Label] = {}
        card_names = [
            ("sync-agent", "Folder watcher"),
            ("analytics-api", "Analytics API"),
            ("analytics-worker", "Video analyzer"),
        ]
        for index, (key, title) in enumerate(card_names):
            card = tk.Frame(
                cards,
                bg=COLORS["surface"],
                highlightbackground=COLORS["border"],
                highlightthickness=1,
                padx=20,
                pady=18,
            )
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 10, 0))
            cards.columnconfigure(index, weight=1)
            # Accent top border strip
            accent = tk.Frame(card, bg=COLORS["primary"], height=3)
            accent.pack(fill="x", pady=(0, 10))
            tk.Label(
                card,
                text=title.upper(),
                bg=COLORS["surface"],
                fg=COLORS["ink_dim"],
                font=("Segoe UI Semibold", 9),
            ).pack(anchor="w")
            label = tk.Label(
                card,
                text="Checking…",
                bg=COLORS["surface"],
                fg=COLORS["ink"],
                font=("Segoe UI Semibold", 16),
            )
            label.pack(anchor="w", pady=(8, 0))
            self.service_labels[key] = label

        active_card = tk.Frame(
            self.dashboard,
            bg=COLORS["surface"],
            highlightbackground=COLORS["primary_glow"],
            highlightthickness=1,
            padx=20,
            pady=16,
        )
        active_card.pack(fill="x", pady=(14, 18))
        tk.Label(
            active_card,
            text="CURRENT ANALYSIS",
            bg=COLORS["surface"],
            fg=COLORS["primary"],
            font=("Segoe UI Semibold", 9),
        ).pack(anchor="w")
        self.job_label = tk.Label(
            active_card,
            text="No active video",
            bg=COLORS["surface"],
            fg=COLORS["ink"],
            font=("Segoe UI Semibold", 14),
        )
        self.job_label.pack(anchor="w", pady=(6, 0))
        self.storage_label = tk.Label(
            active_card,
            text="",
            bg=COLORS["surface"],
            fg=COLORS["ink_dim"],
            font=("Segoe UI", 9),
            wraplength=900,
            justify="left",
        )
        self.storage_label.pack(anchor="w", pady=(4, 0))

        test_card = tk.Frame(
            self.dashboard,
            bg=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            padx=20,
            pady=16,
        )
        test_card.pack(fill="x", pady=(0, 16))

        t_head = tk.Frame(test_card, bg=COLORS["surface"])
        t_head.pack(fill="x", pady=(0, 10))
        tk.Label(
            t_head,
            text="QUICK VIDEO TEST",
            bg=COLORS["surface"],
            fg=COLORS["primary"],
            font=("Segoe UI Semibold", 9),
        ).pack(anchor="w")
        tk.Label(
            t_head,
            text="Copy a test video into a watched folder to verify detection pipeline and queue processing without altering the original.",
            bg=COLORS["surface"],
            fg=COLORS["ink_dim"],
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(2, 0))

        test_row = tk.Frame(test_card, bg=COLORS["surface"])
        test_row.pack(fill="x")
        self.test_directory = tk.StringVar()
        self.test_directory_combo = ttk.Combobox(
            test_row,
            textvariable=self.test_directory,
            state="readonly",
        )
        self.test_directory_combo.pack(side="left", fill="x", expand=True, padx=(0, 12))
        ttk.Button(
            test_row,
            text="▶  Choose video & test",
            style="Primary.TButton",
            command=self.run_video_test,
        ).pack(side="right")

        # Activity log card
        log_card = tk.Frame(
            self.dashboard,
            bg=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            padx=20,
            pady=16,
        )
        log_card.pack(fill="both", expand=True)

        l_head = tk.Frame(log_card, bg=COLORS["surface"])
        l_head.pack(fill="x", pady=(0, 10))
        tk.Label(
            l_head,
            text="●  ACTIVITY & SERVICE LOG",
            bg=COLORS["surface"],
            fg=COLORS["green_text"],
            font=("Segoe UI Semibold", 9),
        ).pack(side="left")
        tk.Label(
            l_head,
            text="Live events, edge worker updates, and detection status",
            bg=COLORS["surface"],
            fg=COLORS["ink_dim"],
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(10, 0))

        self.activity = tk.Text(
            log_card,
            height=6,
            wrap="word",
            bg=COLORS["surface_raised"],
            fg=COLORS["ink_dim"],
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["border_active"],
            font=("Consolas", 9),
            insertbackground=COLORS["ink"],
            state="disabled",
        )
        self.activity.pack(fill="both", expand=True)

    def _build_queue(self) -> None:
        heading = ttk.Frame(self.queue_page)
        heading.pack(fill="x")
        title = ttk.Frame(heading)
        title.pack(side="left", fill="x", expand=True)
        ttk.Label(title, text="Detection queue", font=("Segoe UI Semibold", 18)).pack(
            anchor="w"
        )
        ttk.Label(
            title,
            text="Videos are ordered by the analyzer’s oldest-first discovery. Waiting files do not show a fabricated percentage.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(4, 0))
        ttk.Button(
            heading,
            text="Refresh now",
            style="Secondary.TButton",
            command=self._refresh_status,
        ).pack(side="right", anchor="n")

        current = tk.Frame(
            self.queue_page,
            bg=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            padx=20,
            pady=16,
        )
        current.pack(fill="x", pady=(16, 14))

        meta_row = tk.Frame(current, bg=COLORS["surface"])
        meta_row.pack(fill="x")
        tk.Label(
            meta_row,
            text="CURRENT ANALYSIS",
            bg=COLORS["surface"],
            fg=COLORS["primary"],
            font=("Segoe UI Semibold", 9),
        ).pack(side="left")
        self.queue_status_badge = tk.Label(
            meta_row,
            text="●  IDLE",
            bg=COLORS["green_soft"],
            fg=COLORS["green_text"],
            font=("Segoe UI Semibold", 8),
            padx=10,
            pady=4,
        )
        self.queue_status_badge.pack(side="right")

        current_header = tk.Frame(current, bg=COLORS["surface"])
        current_header.pack(fill="x", pady=(6, 0))
        self.queue_active_label = tk.Label(
            current_header,
            text="No active analysis",
            bg=COLORS["surface"],
            fg=COLORS["ink"],
            font=("Segoe UI Semibold", 13),
        )
        self.queue_active_label.pack(side="left", anchor="w")
        self.cancel_analysis_button = ttk.Button(
            current_header,
            text="Cancel analysis",
            style="Cancel.TButton",
            command=self._cancel_active_analysis,
            state="disabled",
        )
        self.cancel_analysis_button.pack(side="right")

        self.queue_progress = ttk.Progressbar(
            current, orient="horizontal", mode="determinate", maximum=100
        )
        self.queue_progress.pack(fill="x", pady=(10, 6))
        self.queue_summary = tk.Label(
            current,
            text="Waiting for queue data…",
            bg=COLORS["surface"],
            fg=COLORS["ink_dim"],
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
        )
        self.queue_summary.pack(fill="x")

        # ── Filter toolbar ─────────────────────────────────────────────
        filter_bar = tk.Frame(self.queue_page, bg=COLORS["background"])
        filter_bar.pack(fill="x", pady=(10, 4))

        tk.Label(
            filter_bar,
            text="History:",
            bg=COLORS["background"],
            fg=COLORS["ink_dim"],
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(0, 6))

        self._queue_date_var = tk.StringVar(value="All dates")
        self.queue_date_combo = ttk.Combobox(
            filter_bar,
            textvariable=self._queue_date_var,
            state="readonly",
            width=28,
            font=("Segoe UI", 9),
        )
        self.queue_date_combo["values"] = ["All dates"]
        self.queue_date_combo.pack(side="left", padx=(0, 12))
        self.queue_date_combo.bind("<<ComboboxSelected>>", lambda _e: self._apply_queue_filter())

        tk.Label(
            filter_bar,
            text="Status:",
            bg=COLORS["background"],
            fg=COLORS["ink_dim"],
            font=("Segoe UI Semibold", 9),
        ).pack(side="left", padx=(0, 8))

        self._queue_status_var = tk.StringVar(value="All")
        self._status_pills: Dict[str, tk.Label] = {}
        for pill_label in ("All", "Completed", "Failed", "Cancelled"):
            is_active = (pill_label == "All")
            pill_lbl = tk.Label(
                filter_bar,
                text=pill_label,
                bg=COLORS["primary"] if is_active else COLORS["surface_raised"],
                fg="#ffffff" if is_active else COLORS["ink_dim"],
                font=("Segoe UI Semibold", 8),
                padx=11,
                pady=4,
                cursor="hand2",
                relief="flat",
            )
            pill_lbl.pack(side="left", padx=(0, 5))
            pill_lbl.bind("<Button-1>", lambda e, val=pill_label: self._set_status_filter(val))
            pill_lbl.bind("<Enter>", lambda e, val=pill_label: self._on_status_pill_hover(val, True))
            pill_lbl.bind("<Leave>", lambda e, val=pill_label: self._on_status_pill_hover(val, False))
            self._status_pills[pill_label] = pill_lbl

        self._cached_queue_data: Optional[Dict[str, Any]] = None
        # ───────────────────────────────────────────────────────────────

        table_frame = tk.Frame(
            self.queue_page,
            bg=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        table_frame.pack(fill="both", expand=True)
        columns = ("video", "status", "progress", "frames", "details")
        self.queue_tree = ttk.Treeview(
            table_frame, columns=columns, show="tree headings", height=16
        )
        # Hide the phantom tree column, use it only for expand arrows
        self.queue_tree.column("#0", width=0, minwidth=0, stretch=False)
        for key, label, width in (
            ("video", "Video", 240),
            ("status", "Status", 130),
            ("progress", "Progress", 90),
            ("frames", "Frames read / total", 145),
            ("details", "Details", 330),
        ):
            self.queue_tree.heading(key, text=label)
            self.queue_tree.column(key, width=width, anchor="w")
        self.queue_tree.tag_configure("processing", foreground=COLORS["amber_text"], font=("Segoe UI Semibold", 10))
        self.queue_tree.tag_configure("queued", foreground=COLORS["ink"])
        self.queue_tree.tag_configure("waiting", foreground=COLORS["ink_dim"])
        self.queue_tree.tag_configure("cancelled", foreground=COLORS["muted"])
        self.queue_tree.tag_configure("failed", foreground=COLORS["red_text"])
        self.queue_tree.tag_configure("completed", foreground=COLORS["green_text"])
        # Date-group header row style
        self.queue_tree.tag_configure(
            "date_header",
            foreground=COLORS["primary_hover"],
            font=("Segoe UI Semibold", 9),
            background=COLORS["surface_alt"],
        )
        scrollbar = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.queue_tree.yview
        )
        self.queue_tree.configure(yscrollcommand=scrollbar.set)
        self.queue_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _build_setup(self) -> None:
        heading = tk.Frame(self.setup, bg=COLORS["background"])
        heading.pack(fill="x", pady=(0, 16))
        tk.Label(
            heading,
            text="System Settings & Configuration",
            bg=COLORS["background"],
            fg=COLORS["ink"],
            font=("Segoe UI Semibold", 18),
        ).pack(anchor="w")
        tk.Label(
            heading,
            text="Configure video directories to monitor, local analysis storage, and cloud sync credentials.",
            bg=COLORS["background"],
            fg=COLORS["ink_dim"],
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(3, 0))

        # Card 1: Watched folders
        folder_card = tk.Frame(
            self.setup,
            bg=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            padx=20,
            pady=16,
        )
        folder_card.pack(fill="x", pady=(0, 14))

        f_head = tk.Frame(folder_card, bg=COLORS["surface"])
        f_head.pack(fill="x", pady=(0, 10))
        tk.Label(
            f_head,
            text="WATCHED VIDEO FOLDERS",
            bg=COLORS["surface"],
            fg=COLORS["primary"],
            font=("Segoe UI Semibold", 9),
        ).pack(anchor="w")
        tk.Label(
            f_head,
            text="Directories actively monitored for poultry footage (.mp4, .avi, .mkv). Videos are processed in order of arrival.",
            bg=COLORS["surface"],
            fg=COLORS["ink_dim"],
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(2, 0))

        tree_frame = tk.Frame(
            folder_card,
            bg=COLORS["surface_raised"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        tree_frame.pack(fill="x", expand=True)

        self.directory_tree = ttk.Treeview(
            tree_frame,
            columns=("path",),
            show="headings",
            height=3,
            selectmode="extended",
        )
        self.directory_tree.heading("path", text="Watched directory path")
        self.directory_tree.column("path", anchor="w")
        self.directory_tree.pack(fill="x", expand=True)
        self.directory_tree.bind(
            "<<TreeviewSelect>>", self._update_remove_directory_button
        )

        f_buttons = tk.Frame(folder_card, bg=COLORS["surface"])
        f_buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(
            f_buttons,
            text="＋  Add folder…",
            style="Primary.TButton",
            command=self.add_directory,
        ).pack(side="left")
        self.remove_directory_button = ttk.Button(
            f_buttons,
            text="✕  Remove selected",
            style="Secondary.TButton",
            command=self.remove_directories,
            state="disabled",
        )
        self.remove_directory_button.pack(side="left", padx=(8, 0))
        self.clean_directory_button = ttk.Button(
            f_buttons,
            text="🧹  Clean missing",
            style="Secondary.TButton",
            command=self.clean_invalid_directories,
        )
        self.clean_directory_button.pack(side="left", padx=(8, 0))
        tk.Label(
            f_buttons,
            text="Video files in watched directories are never altered or deleted.",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(14, 0))

        # Card 2: Local storage
        storage_card = tk.Frame(
            self.setup,
            bg=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            padx=20,
            pady=16,
        )
        storage_card.pack(fill="x", pady=(0, 14))

        s_head = tk.Frame(storage_card, bg=COLORS["surface"])
        s_head.pack(fill="x", pady=(0, 10))
        tk.Label(
            s_head,
            text="LOCAL STORAGE & RESULTS",
            bg=COLORS["surface"],
            fg=COLORS["primary"],
            font=("Segoe UI Semibold", 9),
        ).pack(anchor="w")
        tk.Label(
            s_head,
            text="Destination path for analyzed detection outputs, behavior JSON metrics, and process log files.",
            bg=COLORS["surface"],
            fg=COLORS["ink_dim"],
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(2, 0))

        s_row = tk.Frame(storage_card, bg=COLORS["surface"])
        s_row.pack(fill="x")
        self.storage_path = tk.StringVar()
        ttk.Entry(s_row, textvariable=self.storage_path).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(
            s_row,
            text="📂  Browse…",
            style="Secondary.TButton",
            command=self.choose_storage,
        ).pack(side="left", padx=(10, 0))

        # Card 3: Cloud sync
        cloud_card = tk.Frame(
            self.setup,
            bg=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            padx=20,
            pady=16,
        )
        cloud_card.pack(fill="x", pady=(0, 14))

        c_head = tk.Frame(cloud_card, bg=COLORS["surface"])
        c_head.pack(fill="x", pady=(0, 10))
        tk.Label(
            c_head,
            text="CLOUD SYNCHRONIZATION (VERCEL)",
            bg=COLORS["surface"],
            fg=COLORS["primary"],
            font=("Segoe UI Semibold", 9),
        ).pack(anchor="w")
        tk.Label(
            c_head,
            text="Optionally forward detection metrics, aggregate behavior counts, and analysis summaries to your online dashboard.",
            bg=COLORS["surface"],
            fg=COLORS["ink_dim"],
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(2, 0))

        self.cloud_sync = tk.BooleanVar()
        ttk.Checkbutton(
            cloud_card,
            text="Sync analysis results, queue status, and databases to Supabase (videos stay local)",
            variable=self.cloud_sync,
        ).pack(anchor="w", pady=(0, 10))

        form_grid = tk.Frame(cloud_card, bg=COLORS["surface"])
        form_grid.pack(fill="x")
        form_grid.columnconfigure(1, weight=1)

        tk.Label(
            form_grid,
            text="Sync URL",
            bg=COLORS["surface"],
            fg=COLORS["ink_dim"],
            font=("Segoe UI Semibold", 9),
        ).grid(row=0, column=0, sticky="w", pady=5)
        self.sync_url = tk.StringVar()
        ttk.Entry(form_grid, textvariable=self.sync_url).grid(
            row=0, column=1, sticky="ew", padx=(14, 0), pady=5
        )

        tk.Label(
            form_grid,
            text="Sync Secret",
            bg=COLORS["surface"],
            fg=COLORS["ink_dim"],
            font=("Segoe UI Semibold", 9),
        ).grid(row=1, column=0, sticky="w", pady=5)
        self.sync_secret = tk.StringVar()
        secret_box = tk.Frame(form_grid, bg=COLORS["surface"])
        secret_box.grid(row=1, column=1, sticky="ew", padx=(14, 0), pady=5)
        secret_box.columnconfigure(0, weight=1)

        self.secret_entry = ttk.Entry(
            secret_box, textvariable=self.sync_secret, show="•"
        )
        self.secret_entry.grid(row=0, column=0, sticky="ew")

        self.secret_visible = False
        def _toggle_secret():
            self.secret_visible = not self.secret_visible
            self.secret_entry.configure(show="" if self.secret_visible else "•")
            self.secret_btn.configure(text="Hide" if self.secret_visible else "Show")

        self.secret_btn = ttk.Button(
            secret_box,
            text="Show",
            style="Secondary.TButton",
            command=_toggle_secret,
            width=6,
        )
        self.secret_btn.grid(row=0, column=1, padx=(6, 0))

        tk.Label(
            cloud_card,
            text="🔒  Cloud sync is optional. Secret is stored locally in ignored agent/.env and is never committed.",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(8, 0))

        # Bottom action bar card
        actions_card = tk.Frame(
            self.setup,
            bg=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            padx=20,
            pady=14,
        )
        actions_card.pack(fill="x")

        ttk.Button(
            actions_card,
            text="💾  Save settings",
            style="Primary.TButton",
            command=self.save_settings,
        ).pack(side="left")

        ttk.Button(
            actions_card,
            text="🛠  Initialize / repair",
            style="Secondary.TButton",
            command=self.initialize_system,
        ).pack(side="left", padx=(10, 0))

        ttk.Button(
            actions_card,
            text="Remove startup",
            style="Secondary.TButton",
            command=self.uninstall_startup,
        ).pack(side="right")

        ttk.Button(
            actions_card,
            text="Enable startup",
            style="Secondary.TButton",
            command=self.install_startup,
        ).pack(side="right", padx=(0, 8))

    def _load_settings_into_form(self) -> None:
        for row in self.directory_tree.get_children():
            self.directory_tree.delete(row)
        for directory in self.settings["directories"]:
            self.directory_tree.insert("", "end", values=(directory,))
        self._update_remove_directory_button()
        self.storage_path.set(str(self.settings["storage_root"]))
        self.cloud_sync.set(bool(self.settings["cloud_sync"]))
        self.sync_url.set(str(self.settings["sync_url"]))
        self.sync_secret.set(str(self.settings["sync_secret"]))
        self._update_test_directory_options()

    def _get_form_settings(self) -> Dict[str, object]:
        directories = [
            self.directory_tree.item(row, "values")[0]
            for row in self.directory_tree.get_children()
        ]
        return {
            **self.settings,
            "directories": directories,
            "storage_root": self.storage_path.get().strip(),
            "cloud_sync": self.cloud_sync.get(),
            "sync_url": self.sync_url.get().strip(),
            "sync_secret": self.sync_secret.get(),
        }

    def _update_test_directory_options(self) -> None:
        directories = [
            self.directory_tree.item(row, "values")[0]
            for row in self.directory_tree.get_children()
        ]
        self.test_directory_combo["values"] = directories
        if directories and self.test_directory.get() not in directories:
            self.test_directory.set(directories[0])
        elif not directories:
            self.test_directory.set("")

    def add_directory(self) -> None:
        directory = filedialog.askdirectory(title="Choose a folder containing videos")
        if not directory:
            return
        existing = {
            self.directory_tree.item(row, "values")[0]
            for row in self.directory_tree.get_children()
        }
        resolved = str(Path(directory).resolve())
        if resolved in existing:
            messagebox.showinfo("Already added", "That folder is already in the watch list.")
            return

        row = self.directory_tree.insert("", "end", values=(resolved,))

        # Prune non-existent paths from previous machines
        for item_row in list(self.directory_tree.get_children()):
            item_path = self.directory_tree.item(item_row, "values")[0]
            if item_row != row and not Path(item_path).is_dir():
                self.directory_tree.delete(item_row)

        directories = [
            self.directory_tree.item(item, "values")[0]
            for item in self.directory_tree.get_children()
        ]
        try:
            write_watch_directories(directories)
        except (OSError, ValueError) as error:
            self.directory_tree.delete(row)
            self._update_test_directory_options()
            messagebox.showerror("Could not add watched folder", str(error))
            return
        self.settings["directories"] = directories
        self._update_test_directory_options()
        if hasattr(self, "_update_remove_directory_button"):
            self._update_remove_directory_button()
        self._log("Added watched folder to saved settings. Restart ChickenSee services to apply the change to the folder watcher.")
        messagebox.showinfo(
            "Watched folder added",
            "The folder was added to saved settings. Its videos will be analyzed after "
            "ChickenSee services restart and the files are stable.",
        )

    def remove_directories(self) -> None:
        selected = self.directory_tree.selection()
        if not selected:
            messagebox.showinfo("Select a folder", "Choose one or more folders to remove.")
            return
        current_directories = [
            self.directory_tree.item(row, "values")[0]
            for row in self.directory_tree.get_children()
        ]
        selected_dirs = {
            self.directory_tree.item(row, "values")[0]
            for row in selected
        }
        remaining = [
            directory
            for directory in current_directories
            if directory not in selected_dirs and Path(directory).is_dir()
        ]
        if not remaining:
            default_dir = (PROJECT_ROOT / "agent" / "data").resolve()
            default_dir.mkdir(parents=True, exist_ok=True)
            remaining = [str(default_dir)]

        for row in list(self.directory_tree.get_children()):
            self.directory_tree.delete(row)
        for directory in remaining:
            self.directory_tree.insert("", "end", values=(directory,))

        try:
            write_watch_directories(remaining)
        except (OSError, ValueError) as error:
            for row in self.directory_tree.get_children():
                self.directory_tree.delete(row)
            for directory in current_directories:
                self.directory_tree.insert("", "end", values=(directory,))
            self._update_remove_directory_button()
            messagebox.showerror("Could not remove watched folder", str(error))
            return
        self.settings["directories"] = remaining
        self._update_test_directory_options()
        self._update_remove_directory_button()
        self._log("Removed watched folder from saved settings. Restart ChickenSee services to apply the change to the folder watcher.")
        messagebox.showinfo(
            "Watched folder removed",
            "The folder was removed from saved settings. Its files were not deleted.\n\n"
            "Restart ChickenSee services to apply the change to the folder watcher.",
        )

    def clean_invalid_directories(self) -> None:
        current_directories = [
            self.directory_tree.item(row, "values")[0]
            for row in self.directory_tree.get_children()
        ]
        valid_directories = [d for d in current_directories if Path(d).is_dir()]
        if not valid_directories:
            default_dir = (PROJECT_ROOT / "agent" / "data").resolve()
            default_dir.mkdir(parents=True, exist_ok=True)
            valid_directories = [str(default_dir)]

        for row in list(self.directory_tree.get_children()):
            self.directory_tree.delete(row)
        for directory in valid_directories:
            self.directory_tree.insert("", "end", values=(directory,))

        try:
            write_watch_directories(valid_directories)
        except (OSError, ValueError) as error:
            messagebox.showerror("Could not clean folders", str(error))
            return

        self.settings["directories"] = valid_directories
        self._update_test_directory_options()
        self._update_remove_directory_button()
        self._log("Cleaned folders from settings that do not exist on this device.")
        messagebox.showinfo(
            "Cleaned invalid folders",
            "Folders from other devices that do not exist on this computer were removed.",
        )

    def _update_remove_directory_button(self, _event=None) -> None:
        if hasattr(self, "remove_directory_button"):
            state = "normal" if self.directory_tree.selection() else "disabled"
            self.remove_directory_button.configure(state=state)

    def choose_storage(self) -> None:
        directory = filedialog.askdirectory(
            title="Choose where ChickenSee saves results and logs",
            initialdir=self.storage_path.get() or str(DEFAULT_STORAGE_ROOT),
        )
        if directory:
            self.storage_path.set(directory)

    def save_settings(self) -> None:
        settings = self._get_form_settings()
        error = validate_agent_settings(settings)
        if error:
            messagebox.showerror("Check your setup", error)
            return
        try:
            write_agent_settings(settings)
        except (OSError, ValueError) as error:
            messagebox.showerror("Could not save settings", str(error))
            return
        self.settings = read_agent_settings()
        self._load_settings_into_form()
        self._log("Settings saved. Restart services to apply folder or sync changes.")
        messagebox.showinfo(
            "Settings saved",
            "Your settings are saved. Restart ChickenSee services to apply changes.",
        )
        self._refresh_status()

    def start_services(self) -> None:
        processes = find_service_processes()
        if processes["supervisor"]:
            self._refresh_status()
            return
        self.starting_services = True
        self._update_power_buttons(processes["supervisor"])
        self._log("Starting ChickenSee services…")
        threading.Thread(target=self._start_services_worker, daemon=True).start()

    def _start_services_worker(self) -> None:
        try:
            launch_log = (
                Path(str(self.settings["storage_root"])).expanduser()
                / "logs"
                / "startup-launch.log"
            )
            process = start_supervisor(
                PROJECT_ROOT, Path(str(self.settings["storage_root"]))
            )
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if find_service_processes()["supervisor"]:
                    self.events.put(
                        ("start_done", True, "ChickenSee services are starting.")
                    )
                    return
                exit_code = process.poll()
                if exit_code is not None and exit_code != 0:
                    break
                time.sleep(0.5)

            detail = ""
            if launch_log.is_file():
                detail = launch_log.read_text(encoding="utf-8", errors="replace")[-3000:]
            if process.poll() is None:
                detail = detail or "The supervisor did not become visible within 20 seconds."
            elif not detail:
                detail = f"The supervisor exited with code {process.returncode}."
            raise RuntimeError(
                f"ChickenSee did not start. Review {launch_log}.\n{detail}"
            )
        except (OSError, RuntimeError) as error:
            self.events.put(("start_done", False, str(error)))

    def stop_services(self) -> None:
        processes = find_service_processes()
        if not processes["supervisor"]:
            messagebox.showwarning(
                "Startup services are not running",
                "There are no ChickenSee startup services for the Stop button to stop.",
            )
            return
        if not messagebox.askyesno(
            "Stop ChickenSee?",
            "This will stop the folder watcher, analytics API, and video analyzer.",
        ):
            return
        self.stopping_services = True
        self._update_power_buttons(processes["supervisor"])
        targets = capture_service_processes()
        try:
            request_stop(
                Path(str(self.settings["storage_root"])).expanduser(),
                processes["supervisor"],
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self.stopping_services = False
            self._update_power_buttons(processes["supervisor"])
            messagebox.showerror("Could not stop services", str(error))
            return
        self._log("Stop requested. Waiting for the supervisor to stop its services…")
        threading.Thread(
            target=self._stop_services_worker,
            args=(targets,),
            daemon=True,
        ).start()

    def _stop_services_worker(
        self, targets: Dict[str, List[Tuple[psutil.Process, float]]]
    ) -> None:
        try:
            used_fallback = stop_processes_after_signal(targets)
            message = (
                "The supervisor did not stop within 15 seconds; stopped the captured "
                "ChickenSee processes directly."
                if used_fallback
                else "ChickenSee stopped."
            )
            self.events.put(("stop_done", True, message))
        except (OSError, RuntimeError) as error:
            self.events.put(("stop_done", False, str(error)))

    def run_video_test(self) -> None:
        if not find_service_processes()["analytics-worker"]:
            messagebox.showerror(
                "Video analyzer is not running",
                "The analyzer starts automatically at Windows sign-in. Wait for it to appear as Running before testing a video.",
            )
            return
        target = self.test_directory.get()
        if not target or not Path(target).is_dir():
            messagebox.showerror("Choose a watch folder", "Add and select an existing watched folder first.")
            return
        source = filedialog.askopenfilename(
            title="Choose a short sample video",
            filetypes=[("MP4 videos", "*.mp4"), ("All files", "*.*")],
        )
        if not source:
            return
        try:
            destination_dir = Path(target).resolve()
            source_path = Path(source).resolve()
            if source_path.parent == destination_dir:
                messagebox.showinfo(
                    "Already in watch folder",
                    "This video is already in the selected watch folder. The worker will pick it up automatically.",
                )
                return
            destination = destination_dir / (
                f"chickensee-test-{datetime.now():%Y%m%d-%H%M%S-%f}{source_path.suffix.lower()}"
            )
            shutil.copy2(source_path, destination)
        except OSError as error:
            messagebox.showerror("Could not copy the test video", str(error))
            return
        self._log(f"Test video copied to {destination}. Analysis starts when the file is stable.")
        messagebox.showinfo(
            "Test video queued",
            "A copy of the video was placed in the watch folder.\n\n"
            "The worker will analyze it after the file stops changing. "
            "Check the current job and results folders on the Dashboard.",
        )

    def initialize_system(self) -> None:
        if not messagebox.askyesno(
            "Initialize ChickenSee",
            "This checks Python and Node.js dependencies and installs missing packages. "
            "An internet connection may be required.",
        ):
            return
        self._set_busy(True)
        self.events.put(("log", "Checking model and runtime dependencies…"))
        threading.Thread(target=self._initialize_worker, daemon=True).start()

    def _initialize_worker(self) -> None:
        try:
            python = sys.executable
            if not Path(python).exists():
                raise RuntimeError(f"Python executable not found: {python}")
            model = PROJECT_ROOT / "custom_poultry.pt"
            if not model.is_file():
                raise RuntimeError(f"Custom model weights are missing: {model}")

            import_check = [
                python,
                "-c",
                "import cv2, fastapi, psutil, sqlalchemy, ultralytics, uvicorn",
            ]
            if self._run_logged_command(import_check, PROJECT_ROOT) != 0:
                requirements = PROJECT_ROOT / "analytics" / "requirements.txt"
                self.events.put(("log", "Installing Python analytics dependencies…"))
                if self._run_logged_command(
                    [python, "-m", "pip", "install", "-r", str(requirements)],
                    PROJECT_ROOT,
                ) != 0:
                    raise RuntimeError("Python dependency installation failed. See the activity log.")

            npm = shutil.which("npm")
            if not npm:
                raise RuntimeError("Node.js / npm was not found. Install Node.js 18 or newer, then retry.")
            node_modules = AGENT_DIR / "node_modules"
            node = shutil.which("node")
            if not node:
                raise RuntimeError("Node.js was not found. Install Node.js 18 or newer, then retry.")
            node_check = subprocess.run(
                [
                    node,
                    "--input-type=module",
                    "-e",
                    "import('chokidar'); import('dotenv')",
                ],
                cwd=AGENT_DIR,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if not node_modules.is_dir() or node_check.returncode != 0:
                self.events.put(("log", "Installing sync-agent dependencies…"))
                if self._run_logged_command([npm, "ci"], AGENT_DIR) != 0:
                    raise RuntimeError("Sync-agent dependency installation failed. See the activity log.")

            self.events.put(("log", "ChickenSee initialization completed successfully."))
            self.events.put(("initialize_done", True, "Setup is ready. Save your settings, then start services."))
        except (OSError, RuntimeError) as error:
            self.events.put(("log", f"Initialization failed: {error}"))
            self.events.put(("initialize_done", False, str(error)))

    def _run_logged_command(self, command: List[str], working_dir: Path) -> int:
        self.events.put(("log", f"Running: {Path(command[0]).name} {' '.join(command[1:])}"))
        process = subprocess.Popen(
            command,
            cwd=working_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if process.stdout:
            for line in process.stdout:
                clean = line.rstrip()
                if clean:
                    self.events.put(("log", clean))
        return process.wait()

    def install_startup(self) -> None:
        settings = self._get_form_settings()
        error = validate_agent_settings(settings)
        if error:
            messagebox.showerror("Finish setup first", error)
            return
        try:
            write_agent_settings(settings)
        except (OSError, ValueError) as error:
            messagebox.showerror("Could not save settings", str(error))
            return
        self._run_installer("install-startup.ps1", "Startup and desktop shortcuts installed.")

    def uninstall_startup(self) -> None:
        self._run_installer("uninstall-startup.ps1", "Automatic startup removed.")

    def _run_installer(self, script: str, success_message: str) -> None:
        script_path = PROJECT_ROOT / "startup" / script
        try:
            process = subprocess.Popen(
                [
                    POWERSHELL,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script_path),
                ],
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as error:
            messagebox.showerror("Could not update startup", str(error))
            return
        self._set_busy(True)
        threading.Thread(
            target=self._read_process_output,
            args=(process, success_message),
            daemon=True,
        ).start()

    def _read_process_output(self, process: subprocess.Popen, success_message: str) -> None:
        if process.stdout:
            for line in process.stdout:
                clean = line.strip()
                if clean:
                    self.events.put(("log", clean))
        return_code = process.wait()
        self.events.put(("startup_done", return_code == 0, success_message))

    def open_logs(self) -> None:
        root = Path(str(self._get_form_settings()["storage_root"])).expanduser() / "logs"
        try:
            root.mkdir(parents=True, exist_ok=True)
            os.startfile(str(root))
        except OSError as error:
            messagebox.showerror("Could not open logs", str(error))

    def open_api_docs(self) -> None:
        webbrowser.open("http://127.0.0.1:8000/docs")

    def _refresh_status(self) -> None:
        if self.refresh_in_progress:
            self.refresh_pending = True
            self.queue_summary.configure(text="Refresh requested; updating after the current check…")
            return
        self.refresh_in_progress = True
        threading.Thread(target=self._status_worker, daemon=True).start()

    def _cancel_active_analysis(self) -> None:
        job_id = self.active_job_id
        if job_id is None or self.cancel_in_progress:
            return
        if not messagebox.askyesno(
            "Cancel analysis?",
            "Stop analyzing the current video? The original video will remain in its watch folder, "
            "but the incomplete detected video will be discarded.",
        ):
            return
        self.cancel_in_progress = True
        self.cancel_analysis_button.configure(state="disabled", text="Cancelling…")
        threading.Thread(
            target=self._cancel_analysis_worker,
            args=(job_id,),
            daemon=True,
        ).start()

    def _cancel_analysis_worker(self, job_id: int) -> None:
        request = urllib.request.Request(
            f"http://127.0.0.1:8000/jobs/{job_id}/cancel",
            data=b"",
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.events.put(("cancel_done", True, payload.get("message", "Cancellation requested.")))
        except urllib.error.HTTPError as error:
            try:
                detail = json.loads(error.read().decode("utf-8")).get("detail", str(error))
            except (json.JSONDecodeError, OSError):
                detail = str(error)
            self.events.put(("cancel_done", False, detail))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as error:
            self.events.put(("cancel_done", False, f"Could not request cancellation: {error}"))

    def _status_worker(self) -> None:
        statuses = find_service_processes()
        health = None
        health_error = None
        queue_data = None
        queue_error = None
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=2) as response:
                health = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as error:
            health_error = str(error)
        if health:
            try:
                with urllib.request.urlopen(QUEUE_URL, timeout=4) as response:
                    queue_data = json.loads(response.read().decode("utf-8"))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as error:
                queue_error = str(error)
        else:
            queue_error = health_error or "Analytics API is unavailable."
        self.events.put(("status", statuses, health, health_error, queue_data, queue_error))

    def _schedule_refresh(self) -> None:
        self._refresh_status()
        self.root.after(REFRESH_INTERVAL_MS, self._schedule_refresh)

    def _drain_events(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] == "log":
                self._log(event[1])
            elif event[0] == "status":
                self.refresh_in_progress = False
                self._display_status(event[1], event[2], event[3])
                self._display_queue(event[4], event[5])
                if self.refresh_pending:
                    self.refresh_pending = False
                    self.root.after(0, self._refresh_status)
            elif event[0] == "cancel_done":
                self.cancel_in_progress = False
                self.cancel_analysis_button.configure(text="Cancel analysis")
                if event[1]:
                    self._log(event[2])
                else:
                    messagebox.showerror("Could not cancel analysis", event[2])
                self._refresh_status()
            elif event[0] == "initialize_done":
                self._set_busy(False)
                if event[1]:
                    messagebox.showinfo("Initialization complete", event[2])
                else:
                    messagebox.showerror("Initialization failed", event[2])
            elif event[0] == "stop_done":
                self.stopping_services = False
                if event[1]:
                    self._log(event[2])
                else:
                    self._log(f"Stop failed: {event[2]}")
                    messagebox.showerror("Could not stop ChickenSee", event[2])
                self._refresh_status()
            elif event[0] == "start_done":
                self.starting_services = False
                if event[1]:
                    self._log(event[2])
                else:
                    self._log(f"Start failed: {event[2]}")
                    messagebox.showerror("Could not start ChickenSee", event[2])
                self._refresh_status()
            elif event[0] == "startup_done":
                self._set_busy(False)
                if event[1]:
                    messagebox.showinfo("Startup settings updated", event[2])
                else:
                    messagebox.showerror(
                        "Startup setup failed",
                        "The startup installer failed. See the activity log for details.",
                    )
        self.root.after(250, self._drain_events)

    def _display_status(self, statuses: Dict[str, List[int]], health, health_error) -> None:
        active = []
        for key, label in (
            ("sync-agent", "Folder watcher"),
            ("analytics-api", "Analytics API"),
            ("analytics-worker", "Video analyzer"),
        ):
            pids = statuses[key]
            label_text = f"Running · PID {pids[0]}" if pids else "Stopped"
            color = COLORS["green"] if pids else COLORS["red"]
            self.service_labels[key].configure(text=label_text, fg=color)
            if pids:
                active.append(label)

        if len(active) == 3:
            self.header_status.configure(
                text="●  All services running",
                bg=COLORS["green_soft"],
                fg=COLORS["green_text"],
            )
        elif active:
            self.header_status.configure(
                text=f"●  {len(active)} of 3 services running",
                bg=COLORS["amber_soft"],
                fg=COLORS["amber_text"],
            )
        else:
            self.header_status.configure(
                text="●  Services stopped",
                bg=COLORS["red_soft"],
                fg=COLORS["red_text"],
            )

        if health:
            job = health.get("system", {}).get("active_processing_job")
            self.job_label.configure(
                text=job or "Startup services are running; no video is being analyzed right now"
            )
            self.storage_label.configure(
                text=(
                    f"Completed: {health.get('storage', {}).get('completed_jobs', 0)}"
                    f"    Failed: {health.get('storage', {}).get('failed_jobs', 0)}"
                    f"    Results: {self.settings['storage_root']}"
                    f"    Cloud sync: {'On' if self.settings['cloud_sync'] else 'Off'}"
                )
            )
        else:
            self.job_label.configure(
                text="Analytics API is not responding" if health_error else "Waiting for API…"
            )
            self.storage_label.configure(
                text="Services start automatically after Windows sign-in."
            )

        self._update_power_buttons(statuses["supervisor"])

    def _update_power_buttons(self, supervisor_pids: List[int]) -> None:
        busy = self.starting_services or self.stopping_services
        if self.starting_services:
            self.start_button.configure(
                state="disabled", text="Starting ChickenSee…"
            )
        else:
            self.start_button.configure(
                state="disabled" if supervisor_pids or busy else "normal",
                text="▶  Start ChickenSee",
            )
        if self.stopping_services:
            self.stop_button.configure(
                state="disabled", text="Stopping ChickenSee…"
            )
        else:
            self.stop_button.configure(
                state="normal" if supervisor_pids and not busy else "disabled",
                text="⏻  Stop ChickenSee",
            )

    def _display_queue(self, queue_data, queue_error) -> None:
        for item_id in self.queue_tree.get_children():
            self.queue_tree.delete(item_id)
        if "queue" in getattr(self, "tab_buttons", {}):
            items_list = queue_data.get("items", []) if queue_data else []
            badge_str = f" ({len(items_list)})" if items_list else ""
            self.tab_buttons["queue"].configure(text=f"⚡  Queue{badge_str}")

        if not queue_data:
            self.queue_active_label.configure(text="Queue Offline — Services Stopped or Starting")
            if hasattr(self, "queue_status_badge"):
                self.queue_status_badge.configure(
                    text="●  OFFLINE", bg=COLORS["red_soft"], fg=COLORS["red_text"]
                )
            self.queue_progress.configure(value=0)
            self.queue_summary.configure(
                text="Start ChickenSee services on the Dashboard tab to begin automatic video detection and queue processing."
            )
            return

        items = queue_data.get("items", [])
        active = next(
            (item for item in items if item.get("status") == "PROCESSING"), None
        )
        if active:
            self.active_job_id = active.get("job_id")
            percentage = active.get("progress_percent")
            progress_text = format_queue_progress("PROCESSING", percentage)
            self.queue_active_label.configure(
                text=f"{active.get('video_filename', 'Video')}  ·  {progress_text}"
            )
            if hasattr(self, "queue_status_badge"):
                self.queue_status_badge.configure(
                    text="●  PROCESSING", bg=COLORS["amber_soft"], fg=COLORS["amber_text"]
                )
            self.queue_progress.configure(value=percentage or 0)
            self.cancel_analysis_button.configure(
                state="disabled" if self.cancel_in_progress else "normal",
                text="Cancelling…" if self.cancel_in_progress else "Cancel analysis",
            )
        else:
            self.active_job_id = None
            self.queue_active_label.configure(text="No active analysis — Standby")
            if hasattr(self, "queue_status_badge"):
                self.queue_status_badge.configure(
                    text="●  IDLE", bg=COLORS["green_soft"], fg=COLORS["green_text"]
                )
            self.queue_progress.configure(value=0)
            self.cancel_analysis_button.configure(state="disabled", text="Cancel analysis")

        summary = queue_data.get("summary", {})
        directory_count = len(queue_data.get("watch_directories", []))
        refresh_time = datetime.now().strftime("%H:%M:%S")
        self.queue_summary.configure(
            text=(
                f"{summary.get('processing', 0)} processing  ·  "
                f"{summary.get('queued', 0)} queued  ·  "
                f"{summary.get('waiting', 0)} waiting for stable files  ·  "
                f"{summary.get('completed', 0)} completed  ·  "
                f"{summary.get('failed', 0)} failed  ·  "
                f"{summary.get('cancelled', 0)} cancelled  ·  "
                f"Watching {directory_count} folders  ·  Updated {refresh_time}"
            )
        )
        missing_directories = queue_data.get("missing_watch_directories", [])
        if missing_directories:
            self.queue_summary.configure(
                text=(
                    f"{self.queue_summary.cget('text')}  ·  Missing folders: "
                    + ", ".join(missing_directories)
                )
            )
        for warning in queue_data.get("warnings", []):
            self.queue_summary.configure(text=f"{self.queue_summary.cget('text')}  ·  {warning}")

        self._cached_queue_data = queue_data
        self._render_queue_tree()

    # ── Filter helpers ─────────────────────────────────────────────────────

    def _set_status_filter(self, status: str) -> None:
        """Update selected status and re-filter treeview."""
        self._queue_status_var.set(status)
        self._update_status_pills()
        self._apply_queue_filter()

    def _update_status_pills(self) -> None:
        """Sync pill button styling with current active status."""
        current = self._queue_status_var.get()
        pills = getattr(self, "_status_pills", {})
        for name, lbl in pills.items():
            if name == current:
                lbl.configure(bg=COLORS["primary"], fg="#ffffff")
            else:
                lbl.configure(bg=COLORS["surface_raised"], fg=COLORS["ink_dim"])

    def _on_status_pill_hover(self, status: str, is_hover: bool) -> None:
        if status == self._queue_status_var.get():
            return
        lbl = getattr(self, "_status_pills", {}).get(status)
        if lbl:
            if is_hover:
                lbl.configure(bg=COLORS["border"], fg=COLORS["ink"])
            else:
                lbl.configure(bg=COLORS["surface_raised"], fg=COLORS["ink_dim"])

    def _apply_queue_filter(self) -> None:
        """Re-render the treeview from the cached data with the current filter."""
        self._update_status_pills()
        if self._cached_queue_data is not None:
            self._render_queue_tree()

    def _render_queue_tree(self) -> None:
        """Clear and repopulate the queue treeview respecting current date/status filters."""
        queue_data = self._cached_queue_data
        if not queue_data:
            return

        for item_id in self.queue_tree.get_children():
            self.queue_tree.delete(item_id)

        items = queue_data.get("items", [])
        HISTORY_STATUSES = {"COMPLETED", "FAILED", "CANCELLED"}
        active_items = [i for i in items if i.get("status", "") not in HISTORY_STATUSES]
        history_items = [i for i in items if i.get("status", "") in HISTORY_STATUSES]

        selected_date = getattr(self, "_queue_date_var", None)
        selected_date = selected_date.get() if selected_date else "All dates"
        selected_status = getattr(self, "_queue_status_var", None)
        selected_status = selected_status.get() if selected_status else "All"

        def _item_date_label(item: dict) -> str:
            for field in ("completed_at", "queued_at", "created_at"):
                raw = item.get(field)
                if raw:
                    try:
                        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                        local_date = dt.astimezone().date()
                        today = datetime.now().date()
                        delta = (today - local_date).days
                        if delta == 0:
                            return "Today  —  " + local_date.strftime("%B %d, %Y")
                        elif delta == 1:
                            return "Yesterday  —  " + local_date.strftime("%B %d, %Y")
                        else:
                            return local_date.strftime("%A, %B %d, %Y")
                    except Exception:
                        pass
            return "Date Unknown"

        # Apply status filter to history items
        if selected_status != "All":
            history_items = [
                i for i in history_items
                if i.get("status", "").upper() == selected_status.upper()
            ]

        # Build all date groups so we can populate the dropdown
        from collections import OrderedDict
        all_date_groups: OrderedDict = OrderedDict()
        for item in [i for i in items if i.get("status", "") in HISTORY_STATUSES]:
            lbl = _item_date_label(item)
            all_date_groups.setdefault(lbl, [])

        combo_values = ["All dates"] + list(all_date_groups.keys())
        if hasattr(self, "queue_date_combo"):
            current_val = self._queue_date_var.get()
            self.queue_date_combo["values"] = combo_values
            # If the previously selected date no longer exists, reset to All
            if current_val not in combo_values:
                self._queue_date_var.set("All dates")
                selected_date = "All dates"

        # Apply date filter to history items
        if selected_date != "All dates":
            history_items = [
                i for i in history_items
                if _item_date_label(i) == selected_date
            ]

        # Re-group filtered history by date
        date_groups: OrderedDict = OrderedDict()
        for item in history_items:
            lbl = _item_date_label(item)
            date_groups.setdefault(lbl, []).append(item)

        # ── Render active items (no grouping) ──────────────────────────
        for item in active_items:
            status = item.get("status", "UNKNOWN")
            progress = format_queue_progress(status, item.get("progress_percent"))
            frames = "—"
            if item.get("progress_frames") is not None and item.get("total_frames"):
                frames = f"{item['progress_frames']} / {item['total_frames']}"
            details = item.get("details") or item.get("video_path") or ""
            iid = f"item_{item['item_id']}"
            self.queue_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    item.get("video_filename", ""),
                    status.replace("_", " ").title(),
                    progress,
                    frames,
                    details,
                ),
                tags=(status.lower(),),
            )

        # ── Render history under collapsible date-group headers ────────
        for date_label, group in date_groups.items():
            group_iid = f"dg_{date_label.replace(' ', '_').replace(',', '').replace('—', '-')}"
            self.queue_tree.insert(
                "",
                "end",
                iid=group_iid,
                open=True,
                values=(
                    f"📅  {date_label}",
                    f"{len(group)} item{'s' if len(group) != 1 else ''}",
                    "", "", "",
                ),
                tags=("date_header",),
            )
            for item in group:
                status = item.get("status", "UNKNOWN")
                progress = format_queue_progress(status, item.get("progress_percent"))
                frames = "—"
                if item.get("progress_frames") is not None and item.get("total_frames"):
                    frames = f"{item['progress_frames']} / {item['total_frames']}"
                details = item.get("details") or item.get("video_path") or ""
                iid = f"item_{item['item_id']}"
                self.queue_tree.insert(
                    group_iid,
                    "end",
                    iid=iid,
                    values=(
                        item.get("video_filename", ""),
                        status.replace("_", " ").title(),
                        progress,
                        frames,
                        details,
                    ),
                    tags=(status.lower(),),
                )


    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for widget in self.setup.winfo_children():
            self._set_widget_state(widget, state)

    def _set_widget_state(self, widget, state: str) -> None:
        try:
            if isinstance(widget, (ttk.Button, ttk.Entry, ttk.Checkbutton, ttk.Combobox)):
                widget.configure(state=state)
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._set_widget_state(child, state)

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.activity.configure(state="normal")
        self.activity.insert("end", f"[{timestamp}] {message}\n")
        self.activity.see("end")
        self.activity.configure(state="disabled")


def main() -> None:
    root = tk.Tk()
    try:
        ChickenSeeDesktop(root)
    except Exception as error:
        root.withdraw()
        messagebox.showerror("ChickenSee Error", f"ChickenSee encountered an error while starting:\n\n{error}")
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
