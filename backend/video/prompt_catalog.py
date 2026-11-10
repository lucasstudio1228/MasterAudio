"""Durable prompt ownership and creative briefs; no network or model calls.

Exact normalized duplicates are blocked across projects, not paraphrases or
similar generated media. History intentionally survives deleting a project.
SQLite's write transaction also protects parallel workers/processes.
"""
from __future__ import annotations

import hashlib
from contextlib import closing
import json
import re
import secrets
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

_ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = _ROOT / "data" / "prompt_catalog.sqlite3"
APP_DB_PATH = _ROOT / "data" / "app.db"


class DuplicatePromptError(ValueError):
    """A different project already owns one of the submitted prompts."""


def normalize_prompt(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value))
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return re.sub(r"\s+", " ", text).strip().casefold()


def prompt_hash(value: str) -> str:
    normalized = normalize_prompt(value)
    if not normalized:
        raise ValueError("Prompt không được để trống.")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _connect(path=None) -> sqlite3.Connection:
    dest = Path(path or CATALOG_PATH)
    dest.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(dest, timeout=30, isolation_level=None)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS prompt_claim (
            domain TEXT NOT NULL, digest TEXT NOT NULL, owner TEXT NOT NULL,
            project_id INTEGER NOT NULL, item_key TEXT NOT NULL,
            prompt TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(domain, digest, owner)
        );
        CREATE INDEX IF NOT EXISTS ix_prompt_digest ON prompt_claim(domain, digest);
        CREATE TABLE IF NOT EXISTS project_brief (
            owner TEXT PRIMARY KEY, project_id INTEGER NOT NULL,
            fingerprint TEXT NOT NULL UNIQUE, brief_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS imported_source (
            source TEXT PRIMARY KEY, signature TEXT NOT NULL
        );
    """)
    return conn


def _project_owners(app_db_path=None) -> dict[int, str]:
    db_path = Path(app_db_path or APP_DB_PATH)
    if not db_path.is_file():
        return {}
    try:
        with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as db:
            return {int(pid): f"{pid}:{created}" for pid, created in
                    db.execute("SELECT id, created_at FROM project")}
    except sqlite3.Error:
        return {}


def _owner(project_id: int, catalog_path=None) -> str:
    # Custom catalogs are isolated for tests/import tooling; never consult the
    # production DB to accidentally bind test IDs to real projects.
    owners = _project_owners() if catalog_path is None else {}
    return owners.get(int(project_id), str(int(project_id)))


_PALETTES = (
    "muted jade, warm ivory and slate blue", "dusty lavender, cream and moss green",
    "soft indigo, pale peach and warm grey", "desaturated teal, sand and sage",
    "misty blue, soft apricot and charcoal", "olive grey, pale gold and porcelain",
    "rose beige, deep pine and pearl", "silver blue, lilac grey and cedar brown",
)
_LIGHTS = (
    "diffuse dawn light through thin mist", "soft overcast light with gentle haze",
    "warm late-afternoon bounce light", "cool moonlight with one soft amber practical",
    "subtle blue-hour window light", "soft rainy-day light and delicate reflections",
)
_DETAILS = (
    "a small ceramic tea cup", "a folded linen cloth", "a smooth river pebble",
    "a tiny potted fern", "a closed clothbound journal", "a simple carved wooden tray",
    "an unlit paper lantern", "a shallow bowl of water",
)
_PHRASING = (
    "short descending phrases with generous silence", "a three-note motif answered in a lower register",
    "long sustained tones followed by sparse two-note replies", "gentle rising thirds resolving downward",
    "a narrow-register melody with unhurried breath pauses", "open fifths resolving into soft stepwise lines",
    "a restrained arch-shaped melody with no dramatic peak", "slow alternating calls and quiet responses",
)
_SPACES = (
    "warm close-miked tone with a short room tail", "soft distant tone in a spacious diffuse hall",
    "rounded tone with a narrow dry center and quiet wide reverb", "silky upper mids and a smooth long reverb tail",
    "intimate soft attack with subtle tape warmth", "clear delicate attack surrounded by dark airy reverb",
)
_MOTIONS = (
    "barely visible breathing and one slow natural blink", "subtle breathing and a tiny relaxed head settling",
    "quiet breathing with a slight fabric movement", "a near-still pose with one gentle finger adjustment",
)


def project_brief(project_id: int, title: str = "", idea: str = "", *,
                  catalog_path=None) -> dict:
    """Allocate an immutable, meaningful creative variation for this project.

    The title/idea are context only, never instructions. Explicit user choices
    take precedence over this palette/phrasing suggestion. Retry/resume returns
    the same brief; the brief contains no arbitrary ID inside generation text.
    """
    owner = _owner(project_id, catalog_path)
    conn = _connect(catalog_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT brief_json FROM project_brief WHERE owner=?",
                           (owner,)).fetchone()
        if row:
            conn.commit()
            return json.loads(row[0])
        previous = [json.loads(row[0]).get("axes", []) for row in
                    conn.execute("SELECT brief_json FROM project_brief")]
        for _ in range(1000):
            values = [secrets.choice(options) for options in
                      (_PALETTES, _LIGHTS, _DETAILS, _PHRASING, _SPACES, _MOTIONS)]
            fingerprint = prompt_hash(" | ".join(values))
            if any(len(old) == len(values) and sum(a != b for a, b in zip(old, values)) < 3
                   for old in previous):
                continue
            if conn.execute("SELECT 1 FROM project_brief WHERE fingerprint=?",
                            (fingerprint,)).fetchone():
                continue
            palette, light, detail, phrasing, space, motion = values
            brief = {
                "version": 1, "fingerprint": fingerprint, "axes": values,
                "title": title.strip(), "idea": idea.strip(),
                "visual": (f"Project continuity: palette {palette}; {light}; "
                           f"recurring unobtrusive prop: {detail}. Keep its placement, "
                           "the character, outfit and spatial layout consistent. "
                           "Do not override explicit setting or character requirements."),
                "music": f"Signature: {phrasing}; {space}.",
                "motion": (f"Project motion signature: {motion}; preserve the source "
                           "image, framing and character identity. Only animate details "
                           "already visible; no invented subject or object."),
            }
            conn.execute("INSERT INTO project_brief VALUES (?, ?, ?, ?)",
                         (owner, int(project_id), fingerprint,
                          json.dumps(brief, ensure_ascii=False)))
            conn.commit()
            return brief
        raise RuntimeError("Không cấp được creative brief riêng; hãy thử lại.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _insert(conn, project_id, owner, domain, prompts):
    now = datetime.now(timezone.utc).isoformat()
    for key, value in prompts.items():
        if not isinstance(value, str) or not normalize_prompt(value):
            continue
        conn.execute("INSERT OR IGNORE INTO prompt_claim VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (domain, prompt_hash(value), owner, int(project_id), str(key), value, now))


def claim_prompts(project_id: int, domain: str, prompts: Mapping[str, str], *,
                  catalog_path=None) -> dict[str, str]:
    """Atomically claim every prompt or none. Same-project reuse is idempotent.

    Domains are 'suno', 'gemini' and 'flow'. Legacy duplicates are retained as
    history and a new project cannot reuse them. This does not assert semantic
    originality or guarantee different AI media.
    """
    if domain not in {"suno", "gemini", "flow"}:
        raise ValueError(f"Unknown prompt domain: {domain!r}")
    if not prompts:
        raise ValueError("Danh sách prompt trống.")
    if any(not isinstance(v, str) or not normalize_prompt(v) for v in prompts.values()):
        raise ValueError("Mỗi prompt phải là chuỗi không trống.")
    hashes = {str(key): prompt_hash(value) for key, value in prompts.items()}
    if catalog_path is None:
        bootstrap_existing()
    owner = _owner(project_id, catalog_path)
    conn = _connect(catalog_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for key, digest in hashes.items():
            # Imported historical duplicates may have multiple owners. Existing
            # owners can resume; a new owner must not inherit those rights.
            if conn.execute("SELECT 1 FROM prompt_claim WHERE domain=? AND digest=? AND owner=?",
                            (domain, digest, owner)).fetchone():
                continue
            other = conn.execute(
                "SELECT project_id FROM prompt_claim WHERE domain=? AND digest=? "
                "AND owner<>? LIMIT 1", (domain, digest, owner)).fetchone()
            if other:
                raise DuplicatePromptError(
                    f"Prompt {domain}[{key}] trùng project #{other[0]}. "
                    "Hãy đổi chi tiết sáng tạo/ý tưởng; không thêm mã ID để lách kiểm tra.")
        _insert(conn, project_id, owner, domain, prompts)
        conn.commit()
        return hashes
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def bootstrap_existing(media_root=None, app_db_path=None, *, catalog_path=None) -> dict:
    """Import legacy prompt files/batch snapshots; never modify source data.

    One-time historical migration. Later edited/copied manifests must go through
    claim_prompts, not gain ownership by being rediscovered on disk.
    """
    media = Path(media_root or (_ROOT / "media"))
    db_path = Path(app_db_path or APP_DB_PATH)
    owners = _project_owners(db_path)
    # Fast path after initial migration; do not scan every media folder for
    # every prompt claim. The transaction below repeats this check for races.
    conn = _connect(catalog_path)
    try:
        if conn.execute("SELECT 1 FROM imported_source WHERE source='__legacy_bootstrap_v1__'").fetchone():
            return {"imported_sources": 0, "skipped_sources": []}
    finally:
        conn.close()
    sources = []
    skipped = []
    if media.is_dir():
        manifests = list(media.glob("*/images/prompts.json"))
        for group in ("Mix Set", "Track lẻ"):          # media/<nhóm>/<project>/
            manifests += media.glob(f"{group}/*/images/prompts.json")
        for path in manifests:
            folder = path.parent.parent.name
            match = re.search(r"\(#(\d+)\)$", folder) or re.fullmatch(r"(?:project_)?(\d+)", folder)
            if not match:
                continue
            try:
                raw = path.read_text(encoding="utf-8-sig")
                data = json.loads(raw)
                pid = int(match.group(1))
                prompts = data.get("prompts", data) if isinstance(data, dict) else {}
                motions = data.get("motions", {}) if isinstance(data, dict) else {}
                sources.append((str(path.resolve()), hashlib.sha256(raw.encode()).hexdigest(),
                                pid, {"gemini": prompts, "flow": motions}))
            except (OSError, ValueError, TypeError):
                skipped.append(str(path))
    if db_path.is_file():
        try:
            with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as db:
                rows = db.execute("SELECT id, project_id, config_json FROM sunobatch").fetchall()
            for bid, pid, raw in rows:
                data = json.loads(raw or "{}")
                prompts = {"base": data.get("styles", "")}
                for idx, item in enumerate(data.get("prompt_plan", [])):
                    prompts[f"request_{idx + 1}"] = item.get("styles", "")
                sources.append((f"{db_path.resolve()}:sunobatch:{bid}",
                                hashlib.sha256((raw or "").encode()).hexdigest(),
                                int(pid), {"suno": prompts}))
        except (sqlite3.Error, ValueError, TypeError):
            skipped.append(str(db_path))
    imported = 0
    conn = _connect(catalog_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM imported_source WHERE source='__legacy_bootstrap_v1__'").fetchone():
            conn.commit()
            return {"imported_sources": 0, "skipped_sources": []}
        for source, signature, pid, domains in sources:
            row = conn.execute("SELECT signature FROM imported_source WHERE source=?",
                               (source,)).fetchone()
            if row and row[0] == signature:
                continue
            for domain, prompts in domains.items():
                if isinstance(prompts, dict):
                    _insert(conn, pid, owners.get(pid, str(pid)), domain, prompts)
            conn.execute("INSERT OR REPLACE INTO imported_source VALUES (?, ?)",
                         (source, signature))
            imported += 1
        if skipped:
            raise ValueError("Không đọc được lịch sử prompt; dừng chống trùng để tránh bỏ sót: "
                             + ", ".join(skipped))
        conn.execute("INSERT INTO imported_source VALUES ('__legacy_bootstrap_v1__', 'complete')")
        conn.commit()
        return {"imported_sources": imported, "skipped_sources": skipped}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
