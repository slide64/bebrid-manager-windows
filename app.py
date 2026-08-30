from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock
from typing import Any

import httpx
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_NAME = "Bebrid Magic"
VERSION = "1.0.3"
API_BASE = "https://api.alldebrid.com"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        path = Path(base) / "BebridMagic"
    else:
        path = Path.home() / ".bebrid-magic"
    path.mkdir(parents=True, exist_ok=True)
    return path


CONFIG_PATH = data_dir() / "config.json"
LOG_PATH = data_dir() / "bebrid-magic.log"


def default_destination() -> str:
    p = Path.home() / "Downloads"
    return str(p if p.exists() else Path.home())


def log(message: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


class Config:
    def __init__(self) -> None:
        self.api_key = ""
        self.destination = default_destination()
        self.load()

    def load(self) -> None:
        if not CONFIG_PATH.exists():
            self.save()
            return
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            self.api_key = str(data.get("api_key", "")).strip()
            self.destination = str(data.get("destination", default_destination()))
        except Exception as exc:
            log(f"Erreur lecture config: {exc}")

    def save(self) -> None:
        CONFIG_PATH.write_text(
            json.dumps(
                {
                    "api_key": self.api_key,
                    "destination": self.destination,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# AllDebrid
# ---------------------------------------------------------------------------

class AllDebridError(RuntimeError):
    pass


class AllDebrid:
    def __init__(self, config: Config) -> None:
        self.config = config

    def headers(self) -> dict[str, str]:
        if not self.config.api_key:
            raise AllDebridError("La clé API AllDebrid n'est pas configurée.")
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "User-Agent": f"Bebrid-Magic-Windows/{VERSION}",
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        data: Any = None,
        params: Any = None,
    ) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                response = client.request(
                    method,
                    API_BASE + path,
                    headers=self.headers(),
                    data=data,
                    params=params,
                )
        except httpx.HTTPError as exc:
            raise AllDebridError(f"Connexion AllDebrid impossible : {exc}") from exc

        try:
            payload = response.json()
        except Exception as exc:
            raise AllDebridError(
                f"Réponse AllDebrid illisible (HTTP {response.status_code})."
            ) from exc

        if response.status_code >= 400 or payload.get("status") != "success":
            err = payload.get("error") or {}
            message = err.get("message") or err.get("code") or "Erreur AllDebrid"
            raise AllDebridError(str(message))

        return payload.get("data") or {}

    def user(self) -> dict[str, Any]:
        return self.request("GET", "/v4/user")

    def saved_links(self) -> list[dict[str, Any]]:
        data = self.request("GET", "/v4/user/links")
        links = data.get("links") or []
        return links if isinstance(links, list) else []

    def magnets(self) -> list[dict[str, Any]]:
        data = self.request("POST", "/v4.1/magnet/status")
        magnets = data.get("magnets") or []
        if isinstance(magnets, dict):
            magnets = [magnets]
        return magnets if isinstance(magnets, list) else []

    def magnet_files(self, magnet_id: str) -> list[dict[str, Any]]:
        # AllDebrid attend un tableau de formulaires id[]. Avec httpx, un dict
        # contenant une liste produit bien : id%5B%5D=123. La V1.0.1 utilisait
        # une liste de tuples, ce qui provoquait l'erreur bytes-like object.
        data = self.request(
            "POST",
            "/v4/magnet/files",
            data={"id[]": [str(magnet_id)]},
        )
        magnets = data.get("magnets") or []
        if isinstance(magnets, dict):
            magnets = [magnets]
        if not magnets:
            return []

        magnet = magnets[0]
        error = magnet.get("error")
        if error:
            raise AllDebridError(
                (error or {}).get("message") or "Erreur lors de la lecture du magnet."
            )

        files_tree = magnet.get("files") or []
        if not isinstance(files_tree, list):
            return []
        return self._flatten_file_tree(files_tree)

    def resolve_download(self, source_link: str) -> dict[str, Any]:
        """Transforme un lien sauvegardé/magnet en vrai lien direct débridé."""
        source_link = (source_link or "").strip()
        if not source_link:
            raise AllDebridError("Lien vide.")

        parsed = urllib.parse.urlparse(source_link)
        host = (parsed.hostname or "").lower()
        # Les liens /dl/ déjà générés par AllDebrid sont directement téléchargeables.
        if host.endswith("debrid.it") and "/dl/" in parsed.path:
            return {
                "link": source_link,
                "filename": self._name_from_url(source_link),
                "filesize": 0,
            }

        data = self.request("POST", "/v4/link/unlock", data={"link": source_link})

        delayed = data.get("delayed")
        if delayed:
            return self._wait_delayed(str(delayed), data)

        direct = str(data.get("link") or "").strip()
        if not direct:
            raise AllDebridError("AllDebrid n'a pas retourné de lien de téléchargement.")

        return {
            "link": direct,
            "filename": str(data.get("filename") or self._name_from_url(direct)),
            "filesize": int(data.get("filesize") or 0),
        }

    def _wait_delayed(self, delayed_id: str, initial: dict[str, Any]) -> dict[str, Any]:
        # AllDebrid recommande un polling espacé d'au moins 5 secondes.
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            time.sleep(5)
            data = self.request("POST", "/v4/link/delayed", data={"id": delayed_id})
            status = int(data.get("status") or 0)
            if status == 2 and data.get("link"):
                direct = str(data["link"])
                return {
                    "link": direct,
                    "filename": str(initial.get("filename") or self._name_from_url(direct)),
                    "filesize": int(initial.get("filesize") or 0),
                }
            if status == 3:
                raise AllDebridError("AllDebrid n'a pas pu générer ce lien différé.")
        raise AllDebridError("Délai dépassé pendant la génération du lien AllDebrid.")

    @classmethod
    def _flatten_file_tree(
        cls, nodes: list[Any], prefix: str = ""
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            name = str(node.get("n") or node.get("name") or "Sans nom")
            children = node.get("e")
            if isinstance(children, list):
                out.extend(cls._flatten_file_tree(children, prefix + name + "/"))
                continue
            link = node.get("l") or node.get("link")
            if link:
                out.append(
                    {
                        "link": str(link),
                        "filename": name or cls._name_from_url(str(link)),
                        "display_path": prefix + name,
                        "size": int(node.get("s") or node.get("size") or 0),
                    }
                )
        return out

    @staticmethod
    def _name_from_url(url: str) -> str:
        try:
            path = urllib.parse.urlparse(url).path
            name = urllib.parse.unquote(Path(path).name)
            return name or "telechargement"
        except Exception:
            return "telechargement"


# ---------------------------------------------------------------------------
# Téléchargements
# ---------------------------------------------------------------------------

INVALID_WIN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_name(name: str) -> str:
    value = INVALID_WIN_CHARS.sub("_", (name or "").strip()).rstrip(" .")
    if not value:
        value = "telechargement"
    if Path(value).stem.upper() in RESERVED:
        value = "_" + value
    return value[:240]


def human_bytes(value: int | float) -> str:
    n = float(value or 0)
    units = ["o", "Ko", "Mo", "Go", "To"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024.0
        i += 1
    if i == 0:
        return f"{int(n)} {units[i]}"
    return f"{n:.1f} {units[i]}"


def unique_target(folder: Path, filename: str) -> Path:
    target = folder / safe_name(filename)
    if not target.exists() and not Path(str(target) + ".part").exists():
        return target

    stem, suffix = target.stem, target.suffix
    i = 2
    while True:
        candidate = folder / f"{stem} ({i}){suffix}"
        if not candidate.exists() and not Path(str(candidate) + ".part").exists():
            return candidate
        i += 1


@dataclass
class DownloadTask:
    id: str
    url: str
    filename: str
    destination: str
    status: str = "En attente"
    completed: int = 0
    total: int = 0
    speed: int = 0
    error: str = ""
    cancel: Event = field(default_factory=Event)


class DownloadManager:
    def __init__(self, on_update) -> None:
        self.on_update = on_update
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="bebrid")
        self.lock = Lock()
        self.tasks: dict[str, DownloadTask] = {}
        self.counter = 0

    def add(self, url: str, filename: str, destination: str) -> DownloadTask:
        folder = Path(destination)
        folder.mkdir(parents=True, exist_ok=True)
        target = unique_target(folder, filename)

        with self.lock:
            self.counter += 1
            task = DownloadTask(
                id=str(self.counter),
                url=url,
                filename=target.name,
                destination=str(folder),
            )
            self.tasks[task.id] = task

        self.executor.submit(self._download, task.id)
        self.on_update()
        return task

    def snapshot(self) -> list[DownloadTask]:
        with self.lock:
            return list(self.tasks.values())

    def cancel_task(self, task_id: str) -> None:
        with self.lock:
            task = self.tasks.get(task_id)
            if task:
                task.cancel.set()

    def clear_finished(self) -> None:
        with self.lock:
            ids = [
                tid for tid, task in self.tasks.items()
                if task.status in {"Terminé", "Erreur", "Annulé"}
            ]
            for tid in ids:
                self.tasks.pop(tid, None)
        self.on_update()

    def shutdown(self) -> None:
        with self.lock:
            for task in self.tasks.values():
                task.cancel.set()
        self.executor.shutdown(wait=False, cancel_futures=False)

    def _download(self, task_id: str) -> None:
        with self.lock:
            task = self.tasks[task_id]
            task.status = "Téléchargement"
        self.on_update()

        target = Path(task.destination) / task.filename
        part = Path(str(target) + ".part")
        existing = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": f"Bebrid-Magic-Windows/{VERSION}"}
        if existing:
            headers["Range"] = f"bytes={existing}-"

        try:
            timeout = httpx.Timeout(connect=30, read=None, write=60, pool=60)
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                with client.stream("GET", task.url, headers=headers) as response:
                    response.raise_for_status()

                    content_type = response.headers.get("Content-Type", "").lower()
                    final_host = (response.url.host or "").lower()
                    if "text/html" in content_type and (
                        final_host.endswith("alldebrid.com")
                        or final_host.endswith("alldebrid.fr")
                    ):
                        raise RuntimeError(
                            "AllDebrid a renvoyé une page HTML au lieu du fichier. "
                            "Le lien direct n'a pas été généré correctement."
                        )

                    resume = bool(existing and response.status_code == 206)
                    if not resume:
                        existing = 0

                    total = self._get_total(response, existing)
                    mode = "ab" if resume else "wb"

                    with self.lock:
                        task.total = total
                        task.completed = existing

                    last_time = time.monotonic()
                    last_bytes = existing

                    with part.open(mode) as f:
                        for chunk in response.iter_bytes(1024 * 1024):
                            if task.cancel.is_set():
                                with self.lock:
                                    task.status = "Annulé"
                                    task.speed = 0
                                self.on_update()
                                return

                            if not chunk:
                                continue

                            f.write(chunk)
                            now = time.monotonic()
                            with self.lock:
                                task.completed += len(chunk)
                                if now - last_time >= 0.7:
                                    task.speed = int(
                                        (task.completed - last_bytes) /
                                        max(now - last_time, 0.001)
                                    )
                                    last_bytes = task.completed
                                    last_time = now
                            self.on_update()

            os.replace(part, target)
            with self.lock:
                task.status = "Terminé"
                task.speed = 0
                if not task.total:
                    task.total = task.completed
            self.on_update()

        except Exception as exc:
            log(f"Téléchargement {task.filename}: {exc}")
            with self.lock:
                task.status = "Erreur"
                task.error = str(exc)
                task.speed = 0
            self.on_update()

    @staticmethod
    def _get_total(response: httpx.Response, existing: int) -> int:
        content_range = response.headers.get("Content-Range", "")
        if "/" in content_range:
            total = content_range.rsplit("/", 1)[-1]
            if total.isdigit():
                return int(total)
        length = response.headers.get("Content-Length")
        if length and length.isdigit():
            value = int(length)
            return existing + value if response.status_code == 206 else value
        return 0


# ---------------------------------------------------------------------------
# Interface Windows native
# ---------------------------------------------------------------------------

class App:
    def __init__(self) -> None:
        self.config = Config()
        self.api = AllDebrid(self.config)
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} {VERSION}")
        self.root.geometry("1120x720")
        self.root.minsize(900, 600)

        self.bg_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="bebrid-ui")
        self.downloads = DownloadManager(self.schedule_download_refresh)

        self.saved_link_rows: dict[str, dict[str, Any]] = {}
        self.magnet_rows: dict[str, dict[str, Any]] = {}
        self.magnet_file_rows: dict[str, dict[str, Any]] = {}

        self.status_var = tk.StringVar(value="Démarrage…")
        self.destination_var = tk.StringVar(value=self.config.destination)
        self.api_key_var = tk.StringVar(value="")
        self.download_status_var = tk.StringVar(value="")

        self.setup_style()
        self.build_ui()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(150, self.startup)

    # ----- UI générale -----

    def setup_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 17, "bold"))
        style.configure("Status.TLabel", font=("Segoe UI", 9))
        style.configure("Treeview", rowheight=28, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=(16, 12))
        top.pack(fill="x")

        left = ttk.Frame(top)
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text="Bebrid Magic", style="Title.TLabel").pack(anchor="w")
        ttk.Label(left, textvariable=self.status_var, style="Status.TLabel").pack(anchor="w", pady=(2, 0))

        right = ttk.Frame(top)
        right.pack(side="right")
        ttk.Label(right, text="Destination :").grid(row=0, column=0, padx=(0, 6))
        ttk.Label(right, textvariable=self.destination_var, width=45).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(right, text="Choisir un dossier…", command=self.choose_destination).grid(row=0, column=2)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        self.tab_links = ttk.Frame(self.notebook)
        self.tab_magnets = ttk.Frame(self.notebook)
        self.tab_downloads = ttk.Frame(self.notebook)
        self.tab_settings = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_links, text="Mes liens sauvegardés")
        self.notebook.add(self.tab_magnets, text="Magnets")
        self.notebook.add(self.tab_downloads, text="Téléchargements")
        self.notebook.add(self.tab_settings, text="Paramètres")

        self.build_links_tab()
        self.build_magnets_tab()
        self.build_downloads_tab()
        self.build_settings_tab()

    def build_links_tab(self) -> None:
        toolbar = ttk.Frame(self.tab_links, padding=10)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Actualiser", command=self.refresh_links).pack(side="left")
        ttk.Button(toolbar, text="Télécharger la sélection", command=self.download_selected_link).pack(side="left", padx=8)

        frame = ttk.Frame(self.tab_links)
        frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        cols = ("filename", "size", "host")
        self.links_tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        self.links_tree.heading("filename", text="Fichier")
        self.links_tree.heading("size", text="Taille")
        self.links_tree.heading("host", text="Hébergeur")
        self.links_tree.column("filename", width=650)
        self.links_tree.column("size", width=120, anchor="e")
        self.links_tree.column("host", width=150)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.links_tree.yview)
        self.links_tree.configure(yscrollcommand=scroll.set)
        self.links_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.links_tree.bind("<Double-1>", lambda _e: self.download_selected_link())

    def build_magnets_tab(self) -> None:
        toolbar = ttk.Frame(self.tab_magnets, padding=10)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Actualiser", command=self.refresh_magnets).pack(side="left")
        ttk.Button(toolbar, text="Voir les fichiers", command=self.open_selected_magnet).pack(side="left", padx=8)

        frame = ttk.Frame(self.tab_magnets)
        frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        cols = ("name", "size", "status")
        self.magnets_tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        self.magnets_tree.heading("name", text="Magnet")
        self.magnets_tree.heading("size", text="Taille")
        self.magnets_tree.heading("status", text="État")
        self.magnets_tree.column("name", width=650)
        self.magnets_tree.column("size", width=120, anchor="e")
        self.magnets_tree.column("status", width=180)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.magnets_tree.yview)
        self.magnets_tree.configure(yscrollcommand=scroll.set)
        self.magnets_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.magnets_tree.bind("<Double-1>", lambda _e: self.open_selected_magnet())

    def build_downloads_tab(self) -> None:
        toolbar = ttk.Frame(self.tab_downloads, padding=10)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Ouvrir le dossier", command=self.open_destination).pack(side="left")
        ttk.Button(toolbar, text="Annuler la sélection", command=self.cancel_selected_download).pack(side="left", padx=8)
        ttk.Button(toolbar, text="Nettoyer les terminés", command=self.downloads.clear_finished).pack(side="left")
        ttk.Label(toolbar, textvariable=self.download_status_var).pack(side="right")

        frame = ttk.Frame(self.tab_downloads)
        frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        cols = ("filename", "status", "progress", "speed")
        self.download_tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        self.download_tree.heading("filename", text="Fichier")
        self.download_tree.heading("status", text="État")
        self.download_tree.heading("progress", text="Progression")
        self.download_tree.heading("speed", text="Vitesse")
        self.download_tree.column("filename", width=550)
        self.download_tree.column("status", width=130)
        self.download_tree.column("progress", width=220)
        self.download_tree.column("speed", width=120, anchor="e")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.download_tree.yview)
        self.download_tree.configure(yscrollcommand=scroll.set)
        self.download_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def build_settings_tab(self) -> None:
        outer = ttk.Frame(self.tab_settings, padding=24)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Clé API AllDebrid", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text="La clé est enregistrée uniquement dans %LOCALAPPDATA%\\BebridMagic\\config.json",
        ).pack(anchor="w", pady=(3, 8))

        line = ttk.Frame(outer)
        line.pack(fill="x")
        self.api_entry = ttk.Entry(line, textvariable=self.api_key_var, show="•")
        self.api_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(line, text="Afficher / masquer", command=self.toggle_api_visibility).pack(side="left", padx=8)
        ttk.Button(line, text="Enregistrer et tester", command=self.save_api_key).pack(side="left")

        ttk.Separator(outer).pack(fill="x", pady=24)

        ttk.Label(outer, text=f"Version : {VERSION}").pack(anchor="w")
        ttk.Label(outer, text=f"Configuration : {CONFIG_PATH}").pack(anchor="w", pady=(6, 0))
        ttk.Label(outer, text=f"Journal : {LOG_PATH}").pack(anchor="w", pady=(6, 0))
        ttk.Button(outer, text="Ouvrir le dossier de données", command=self.open_data_dir).pack(anchor="w", pady=(12, 0))

    # ----- tâches asynchrones -----

    def run_async(self, func, on_success=None, busy_text="Chargement…") -> None:
        self.status_var.set(busy_text)

        def worker():
            try:
                result = func()
            except Exception as exc:
                log(f"Erreur: {exc}")
                self.root.after(0, lambda: self.show_error(exc))
                return

            if on_success:
                self.root.after(0, lambda: on_success(result))

        self.bg_executor.submit(worker)

    def startup(self) -> None:
        self.destination_var.set(self.config.destination)
        if not self.config.api_key:
            self.status_var.set("Clé API AllDebrid à configurer")
            self.notebook.select(self.tab_settings)
            return
        self.test_connection(load_after=True)

    def test_connection(self, load_after=False) -> None:
        def success(data):
            user = data.get("user") if isinstance(data.get("user"), dict) else data
            name = user.get("username") or user.get("email") or "AllDebrid"
            self.status_var.set(f"Connecté à AllDebrid · {name}")
            if load_after:
                self.refresh_links()
                self.refresh_magnets()

        self.run_async(self.api.user, success, "Connexion à AllDebrid…")

    def show_error(self, exc: Exception | str) -> None:
        msg = str(exc)
        self.status_var.set(f"Erreur : {msg}")
        messagebox.showerror(APP_NAME, msg, parent=self.root)

    # ----- paramètres -----

    def choose_destination(self) -> None:
        initial = self.config.destination if Path(self.config.destination).exists() else default_destination()
        folder = filedialog.askdirectory(
            parent=self.root,
            title="Choisir le dossier de destination",
            initialdir=initial,
            mustexist=True,
        )
        if not folder:
            return
        self.config.destination = folder
        self.config.save()
        self.destination_var.set(folder)
        self.status_var.set("Dossier de destination modifié")

    def toggle_api_visibility(self) -> None:
        self.api_entry.configure(show="" if self.api_entry.cget("show") else "•")

    def save_api_key(self) -> None:
        value = self.api_key_var.get().strip()
        if not value:
            messagebox.showwarning(APP_NAME, "Colle d'abord ta clé API AllDebrid.", parent=self.root)
            return

        old = self.config.api_key
        self.config.api_key = value

        def success(data):
            self.config.save()
            self.api_key_var.set("")
            self.status_var.set("Clé API enregistrée · connexion validée")
            messagebox.showinfo(APP_NAME, "Clé API valide et enregistrée.", parent=self.root)
            self.refresh_links()
            self.refresh_magnets()

        def worker():
            try:
                return self.api.user()
            except Exception:
                self.config.api_key = old
                raise

        self.run_async(worker, success, "Test de la clé API…")

    # ----- liens sauvegardés -----

    def refresh_links(self) -> None:
        if not self.config.api_key:
            return
        self.run_async(self.api.saved_links, self.populate_links, "Chargement des liens sauvegardés…")

    def populate_links(self, links: list[dict[str, Any]]) -> None:
        self.links_tree.delete(*self.links_tree.get_children())
        self.saved_link_rows.clear()

        for i, item in enumerate(links):
            iid = f"L{i}"
            self.saved_link_rows[iid] = item
            self.links_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    item.get("display_path") or item.get("filename") or "Sans nom",
                    human_bytes(int(item.get("size") or 0)),
                    item.get("host") or "",
                ),
            )

        self.status_var.set(f"{len(links)} lien(s) sauvegardé(s)")

    def download_selected_link(self) -> None:
        selected = self.links_tree.selection()
        if not selected:
            messagebox.showinfo(APP_NAME, "Sélectionne un lien.", parent=self.root)
            return
        item = self.saved_link_rows[selected[0]]
        url = str(item.get("link") or "")
        if not url:
            self.show_error("Le lien sélectionné ne contient aucune URL.")
            return
        filename = str(item.get("filename") or AllDebrid._name_from_url(url))
        self.queue_download(url, filename)

    # ----- magnets -----

    def refresh_magnets(self) -> None:
        if not self.config.api_key:
            return
        self.run_async(self.api.magnets, self.populate_magnets, "Chargement des magnets…")

    def populate_magnets(self, magnets: list[dict[str, Any]]) -> None:
        self.magnets_tree.delete(*self.magnets_tree.get_children())
        self.magnet_rows.clear()

        for i, item in enumerate(magnets):
            iid = f"M{i}"
            self.magnet_rows[iid] = item
            self.magnets_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    item.get("filename") or item.get("name") or f"Magnet {item.get('id', '')}",
                    human_bytes(int(item.get("size") or 0)),
                    item.get("status") or "",
                ),
            )

        self.status_var.set(f"{len(magnets)} magnet(s)")

    def open_selected_magnet(self) -> None:
        selected = self.magnets_tree.selection()
        if not selected:
            messagebox.showinfo(APP_NAME, "Sélectionne un magnet.", parent=self.root)
            return
        item = self.magnet_rows[selected[0]]
        magnet_id = str(item.get("id") or "")
        if not magnet_id:
            self.show_error("Identifiant du magnet manquant.")
            return

        title = str(item.get("filename") or item.get("name") or f"Magnet {magnet_id}")
        self.run_async(
            lambda: self.api.magnet_files(magnet_id),
            lambda files: self.show_magnet_files(title, files),
            "Chargement des fichiers du magnet…",
        )

    def show_magnet_files(self, title: str, files: list[dict[str, Any]]) -> None:
        self.status_var.set(f"{len(files)} fichier(s) dans le magnet")
        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry("900x540")
        win.transient(self.root)

        toolbar = ttk.Frame(win, padding=10)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text=title, font=("Segoe UI", 11, "bold")).pack(side="left")
        ttk.Button(
            toolbar,
            text="Télécharger la sélection",
            command=lambda: self.download_selected_magnet_files(tree, rows, win),
        ).pack(side="right")
        ttk.Button(
            toolbar,
            text="Tout sélectionner",
            command=lambda: tree.selection_set(*tree.get_children()),
        ).pack(side="right", padx=(0, 8))
        ttk.Label(
            toolbar,
            text="Ctrl/Shift + clic pour sélectionner plusieurs fichiers",
        ).pack(side="right", padx=(0, 12))

        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        tree = ttk.Treeview(frame, columns=("name", "size"), show="headings", selectmode="extended")
        tree.heading("name", text="Fichier")
        tree.heading("size", text="Taille")
        tree.column("name", width=700)
        tree.column("size", width=130, anchor="e")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        rows: dict[str, dict[str, Any]] = {}
        for i, item in enumerate(files):
            iid = f"F{i}"
            rows[iid] = item
            tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    item.get("display_path") or item.get("filename") or "Sans nom",
                    human_bytes(int(item.get("size") or 0)),
                ),
            )
        def select_all(_event=None):
            children = tree.get_children()
            if children:
                tree.selection_set(*children)
            return "break"

        tree.bind("<Control-a>", select_all)
        tree.bind("<Control-A>", select_all)
        tree.bind(
            "<Double-1>",
            lambda _e: self.download_selected_magnet_files(tree, rows, win),
        )

    def download_selected_magnet_files(self, tree, rows, win) -> None:
        selected = tree.selection()
        if not selected:
            messagebox.showinfo(APP_NAME, "Sélectionne au moins un fichier.", parent=win)
            return

        queued = 0
        missing_url = 0
        for iid in selected:
            item = rows.get(iid)
            if not item:
                continue
            url = str(item.get("link") or "").strip()
            if not url:
                missing_url += 1
                continue
            filename = str(item.get("filename") or AllDebrid._name_from_url(url))
            self.queue_download(url, filename)
            queued += 1

        if queued:
            self.status_var.set(f"{queued} fichier(s) ajouté(s) au traitement")
        if missing_url:
            messagebox.showwarning(
                APP_NAME,
                f"{missing_url} fichier(s) sélectionné(s) n'ont pas d'URL et ont été ignorés.",
                parent=win,
            )

    # ----- téléchargements -----

    def queue_download(self, url: str, filename: str) -> None:
        # Les liens de /user/links et les liens l des magnets ne sont pas
        # téléchargés tels quels. On demande d'abord à AllDebrid le vrai lien
        # direct via /link/unlock, puis seulement on lance le téléchargement.
        def resolve():
            return self.api.resolve_download(url)

        def resolved(info: dict[str, Any]):
            try:
                final_name = str(info.get("filename") or filename or "telechargement")
                task = self.downloads.add(
                    str(info["link"]),
                    final_name,
                    self.config.destination,
                )
                self.status_var.set(f"Ajouté : {task.filename}")
                self.notebook.select(self.tab_downloads)
            except Exception as exc:
                self.show_error(exc)

        self.run_async(resolve, resolved, f"Débridage de {filename}…")

    def schedule_download_refresh(self) -> None:
        try:
            self.root.after(0, self.refresh_download_tree)
        except Exception:
            pass

    def refresh_download_tree(self) -> None:
        tasks = self.downloads.snapshot()
        selected = self.download_tree.selection()
        selected_id = selected[0] if selected else None

        self.download_tree.delete(*self.download_tree.get_children())
        active = 0
        for task in tasks:
            if task.status in {"En attente", "Téléchargement"}:
                active += 1

            if task.total:
                pct = min(100.0, task.completed * 100.0 / task.total)
                progress = f"{pct:.1f}% · {human_bytes(task.completed)} / {human_bytes(task.total)}"
            else:
                progress = human_bytes(task.completed)

            speed = f"{human_bytes(task.speed)}/s" if task.speed else ""
            status = task.status
            if task.status == "Erreur" and task.error:
                status = "Erreur"

            self.download_tree.insert(
                "",
                "end",
                iid=task.id,
                values=(task.filename, status, progress, speed),
            )

        if selected_id and self.download_tree.exists(selected_id):
            self.download_tree.selection_set(selected_id)

        self.download_status_var.set(f"{active} actif(s) · {len(tasks)} au total")

    def cancel_selected_download(self) -> None:
        selected = self.download_tree.selection()
        if not selected:
            return
        self.downloads.cancel_task(selected[0])

    # ----- système -----

    def open_destination(self) -> None:
        try:
            Path(self.config.destination).mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(self.config.destination)  # type: ignore[attr-defined]
        except Exception as exc:
            self.show_error(exc)

    def open_data_dir(self) -> None:
        try:
            if os.name == "nt":
                os.startfile(str(data_dir()))  # type: ignore[attr-defined]
        except Exception as exc:
            self.show_error(exc)

    def on_close(self) -> None:
        self.downloads.shutdown()
        self.bg_executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()

    def run(self) -> None:
        log(f"Démarrage {APP_NAME} {VERSION}")
        self.root.mainloop()


def main() -> None:
    try:
        App().run()
    except Exception as exc:
        log(f"ERREUR FATALE: {exc}")
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                APP_NAME,
                f"Erreur au démarrage :\n\n{exc}\n\nJournal :\n{LOG_PATH}",
            )
            root.destroy()
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
