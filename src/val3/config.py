"""Project configuration (val3.toml), vault names and entry references."""
import os
import re
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from val3 import Val3Error

CONFIG_FILE = "val3.toml"
_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")
_RESERVED = {"public", "val3_common", "information_schema"}
_REF = re.compile(r"([a-z][a-z0-9_]{0,62})#([1-9][0-9]{0,17})")
DEFAULT_EXTRACTOR_MODEL = "claude-haiku-5-5"
DEFAULT_EMBEDDING_URL = "http://127.0.0.1:11434"
DEFAULT_EMBEDDING_MODEL = "bge-m3"
DEFAULT_FLOOR = 0.60  # measured with bge-m3 on the first adopted project (design 3.3)
DEFAULT_NEIGHBOUR = 0.70  # design 2026-10-24-automatic-admission-design.md D2
DEFAULT_REMEMBER_DAYS = 90  # how long capture remembers a processed transcript record


@dataclass(frozen=True)
class Embedding:
    url: str
    model: str
    floor: float
    neighbour: float | None  # None: `neighbour = false`, the check is off


@dataclass(frozen=True)
class Project:
    root: Path
    name: str
    service: str
    home: str
    linked: tuple[str, ...]
    extractor_model: str = DEFAULT_EXTRACTOR_MODEL
    embedding: Embedding | None = None
    specialized: tuple[str, ...] = ()
    delivery_index: bool = True
    delivery_prompt: bool = True
    remember_days: int = DEFAULT_REMEMBER_DAYS
    extractor_focus: bool = False  # [extractor] focus: only decisions, procedures, pitfalls, accepted exceptions

    @property
    def own(self) -> tuple[str, ...]:
        """The vaults this project writes: home, then the specialized ones."""
        return (self.home, *self.specialized)

    @property
    def vaults(self) -> tuple[str, ...]:
        return (*self.own, *self.linked)

    def writing(self, vault: str) -> "Project":
        """The project as seen from its own vault `vault`: that vault as home, every other vault as linked."""
        if vault not in self.vaults:
            raise Val3Error(f"vault {vault} is not declared in {self.root / CONFIG_FILE}")
        if vault not in self.own:
            raise Val3Error(f"vault {vault} is read only here: write to it from its own project")
        return replace(self, home=vault, specialized=(), linked=tuple(v for v in self.vaults if v != vault))


def check_vault_name(name: object) -> str:
    if (not isinstance(name, str) or not _NAME.fullmatch(name)
            or name in _RESERVED or name.startswith("pg_")):
        raise Val3Error(f"invalid vault name {name!r}: use lowercase letters, digits and '_', "
                        "starting with a letter")
    return name


def parse_ref(text: str) -> tuple[str, int]:
    match = _REF.fullmatch(text.strip())
    if not match:
        raise Val3Error(f"invalid entry reference {text!r}: expected <vault>#<id>")
    return check_vault_name(match[1]), int(match[2])


def find_config(start: Path) -> Path | None:
    start = start.resolve()
    for directory in (start, *start.parents):
        if (directory / CONFIG_FILE).is_file():
            return directory / CONFIG_FILE
    return None


def load_project(start: Path) -> Project:
    path = find_config(start)
    if path is None:
        raise Val3Error(f"no {CONFIG_FILE} found in {start} or its parents")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise Val3Error(f"{path}: {exc}") from None
    except OSError as exc:
        raise Val3Error(f"cannot read {path}: {exc.strerror}") from None
    unknown = set(data) - {"service", "project", "vaults", "extractor", "embedding", "delivery", "capture"}
    if unknown:
        raise Val3Error(f"{path}: unknown keys: {', '.join(sorted(unknown))}")
    service = data.get("service")
    if not isinstance(service, str) or not service.strip():
        raise Val3Error(f"{path}: 'service' must name a pg_service.conf entry")
    name = data.get("project", path.parent.name)
    if not isinstance(name, str) or not name.strip():
        raise Val3Error(f"{path}: 'project' must be a non-empty string")
    vaults = data.get("vaults")
    if not isinstance(vaults, dict) or set(vaults) - {"home", "specialized", "linked"}:
        raise Val3Error(f"{path}: [vaults] must hold 'home' and optionally 'specialized' and 'linked'")
    home = check_vault_name(vaults.get("home"))
    lists = {}
    for key in ("specialized", "linked"):
        names = vaults.get(key, [])
        if not isinstance(names, list):
            raise Val3Error(f"{path}: '{key}' must be a list of vault names")
        lists[key] = tuple(check_vault_name(vault) for vault in names)
    specialized, linked = lists["specialized"], lists["linked"]
    every = (home, *specialized, *linked)
    if len(set(every)) != len(every):
        raise Val3Error(f"{path}: a vault appears twice in [vaults]")
    extractor = data.get("extractor", {})
    if not isinstance(extractor, dict) or set(extractor) - {"model", "focus"}:
        raise Val3Error(f"{path}: [extractor] may only hold 'model' and 'focus'")
    if not isinstance(extractor.get("focus", False), bool):
        raise Val3Error(f"{path}: [extractor] focus must be true or false")
    model = extractor.get("model", DEFAULT_EXTRACTOR_MODEL)
    if not isinstance(model, str) or not model.strip():
        raise Val3Error(f"{path}: [extractor] model must be a model name")
    embedding = data.get("embedding")
    if embedding is not None:
        if not isinstance(embedding, dict) or set(embedding) - {"url", "model", "floor", "neighbour"}:
            raise Val3Error(f"{path}: [embedding] may only hold 'url', 'model', 'floor' and 'neighbour'")
        url = embedding.get("url", DEFAULT_EMBEDDING_URL)
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise Val3Error(f"{path}: [embedding] url must be an http:// or https:// URL")
        embedding_model = embedding.get("model", DEFAULT_EMBEDDING_MODEL)
        if not isinstance(embedding_model, str) or not embedding_model.strip():
            raise Val3Error(f"{path}: [embedding] model must be a model name")
        floor = embedding.get("floor", DEFAULT_FLOOR)
        if isinstance(floor, bool) or not isinstance(floor, (int, float)) or not 0 < floor < 1:
            raise Val3Error(f"{path}: [embedding] floor must be a number between 0 and 1")
        neighbour = embedding.get("neighbour", DEFAULT_NEIGHBOUR)
        if neighbour is False:
            neighbour = None
        elif isinstance(neighbour, bool) or not isinstance(neighbour, (int, float)) or not 0 < neighbour < 1:
            raise Val3Error(f"{path}: [embedding] neighbour must be a number between 0 and 1, or false")
        embedding = Embedding(url.rstrip("/"), embedding_model, float(floor),
                              None if neighbour is None else float(neighbour))
    delivery = data.get("delivery", {})
    if not isinstance(delivery, dict):
        raise Val3Error(f"{path}: [delivery] must be a table")
    if set(delivery) - {"index", "prompt"}:
        raise Val3Error(f"{path}: [delivery] may only hold 'index' and 'prompt', not "
                        f"{', '.join(sorted(set(delivery) - {'index', 'prompt'}))}")
    for key in ("index", "prompt"):
        if not isinstance(delivery.get(key, True), bool):
            raise Val3Error(f"{path}: [delivery] {key} must be true or false")
    capture = data.get("capture", {})
    if not isinstance(capture, dict) or set(capture) - {"remember_days"}:
        raise Val3Error(f"{path}: [capture] may only hold 'remember_days'")
    remember = capture.get("remember_days", DEFAULT_REMEMBER_DAYS)
    if isinstance(remember, bool) or not isinstance(remember, int) or remember < 1:
        raise Val3Error(f"{path}: [capture] remember_days must be a whole number of days, at least 1")
    return Project(path.parent, name, service, home, linked, model, embedding, specialized,
                   delivery.get("index", True), delivery.get("prompt", True), remember,
                   extractor.get("focus", False))


def state_dir() -> Path:
    """Local machine state: queue, watermarks, inbox hashes, warn markers, extraction log."""
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "val3"


def data_dir() -> Path:
    """Local machine data the user keeps: backups and import manifests."""
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "val3"


def create_private(path: Path) -> int:
    """Open a new file readable by its owner only; an existing file is never overwritten."""
    try:
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise Val3Error(f"{path} already exists") from None
    except OSError as exc:
        raise Val3Error(f"cannot create {path}: {exc.strerror}") from None
