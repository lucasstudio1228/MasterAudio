"""
video/suno_selfcheck.py — Mở UI Suno để ĐĂNG NHẬP + TỰ KIỂM TRA / CẬP NHẬT
selector khi giao diện Suno đổi.

Luồng (chạy trong worker đơn của video_job_manager nên độc quyền profile):
  1. Mở trình duyệt (profile riêng .browser_profile_suno, headful) tới /create.
  2. Chờ người dùng đăng nhập (không tự nhập mật khẩu).
  3. Dò từng nhóm selector "page-level" trên trang thật; nhóm nào hỏng thì thử
     các selector DỰ PHÒNG (heal candidates) — cái nào chạy được thì LƯU vào
     video_overrides.json (selectors.suno.<key>). Nhóm không tự đoán được thì
     báo rõ để sửa tay.
  4. Giữ cửa sổ mở để người dùng kiểm tra tới khi bấm "Đóng trình duyệt".

An toàn: KHÔNG bấm Create, KHÔNG tải file, KHÔNG tiêu credit. Chỉ đọc DOM +
best-effort click các tab/expander để lộ phần tử (Advanced, More Options).
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from . import config as vconfig
from .browser_base import BrowserSession, query_first

# Selector luôn hiện trên trang /create khi đã đăng nhập (sau khi chọn tab
# Advanced + mở More Options). Đây là những nhóm KIỂM ĐƯỢC ở trạng thái tĩnh.
PAGE_LEVEL: list[str] = [
    "mode_advanced_tab", "mode_simple_tab", "model_button", "styles_box",
    "create_button", "credits", "lyrics_editor", "more_options",
    "exclude_styles",
]

# Selector page-level nhưng CHỈ hiện khi ở đúng trạng thái phụ (Song Title phải
# cuộn/mở thêm). Không thấy → báo "conditional" (thông tin), KHÔNG tính là
# hỏng/cần-sửa-tay để tránh báo nhầm.
CONDITIONAL: list[str] = [
    "song_title",
]

# Selector chỉ xuất hiện SAU thao tác (mở menu ⋯, popover model, hộp thoại
# Download, luồng Studio) hoặc phụ thuộc dữ liệu (phải có bài trong workspace).
# Không kiểm ở trạng thái tĩnh → báo "skipped" để tránh báo hỏng nhầm.
INTERACTIVE: list[str] = [
    "model_option_v6", "max_mode_off",
    "max_mode_on", "clear_form", "new_workspace", "song_row_play",
    "song_more_options", "download_entry", "download_dialog",
    "download_format_wav", "download_format_mp3", "download_confirm",
    "download_wav", "studio_edit_menu", "open_in_studio",
    "studio_single_track", "studio_export_menu", "studio_full_song",
    "studio_song_saved", "studio_go_to_song", "studio_preparing",
    "open_in_editor", "editor_tempo", "save_as_new_song",
]

# Selector dự phòng thử khi tất cả selector hiện tại của nhóm đều KHÔNG khớp.
# Cái nào khớp (visible) sẽ được đưa lên đầu danh sách và lưu vào overrides.
HEAL_CANDIDATES: dict[str, list[str]] = {
    "styles_box": [
        "[data-testid='create-form-styles-wrapper'] textarea",
        "[data-testid*='styles' i] textarea",
        "textarea[maxlength='1000']",
        "textarea[data-testid='tag-input-textarea']",
        "form textarea[maxlength]",
    ],
    "create_button": [
        "button[aria-label='Create song']",
        "button[aria-label*='Create' i]",
        "button:has-text('Create')",
        "[role=button]:has-text('Create')",
    ],
    "model_button": [
        "button[aria-haspopup='dialog']:right-of(button[aria-label^='Credits remaining'])",
        "button:has-text('v6-mini')", "button:has-text('v6-wild')",
        "button:has-text('v6')", "button:has-text('v5')",
        "button[aria-label*='model' i]",
    ],
    "mode_advanced_tab": [
        "[role=tab]:has-text('Advanced')",
        "button[role=tab]:has-text('Advanced')",
        "button:has-text('Advanced')",
    ],
    "mode_simple_tab": [
        "[role=tab]:has-text('Simple')", "button:has-text('Simple')",
    ],
    "credits": [
        "button[aria-label^='Credits remaining']",
        "*[aria-label*='Credits remaining' i]",
        "*[aria-label*='credit' i]",
    ],
    "lyrics_editor": [
        "[role=textbox][aria-label='Lyrics editor']",
        "div.lyrics-editor-content[contenteditable='true']",
        "[role=textbox][aria-label*='Lyrics' i]",
    ],
    "more_options": [
        "button:has-text('More Options')",
        "[role=button]:has-text('More Options')",
        "button:has-text('More options')",
    ],
    "exclude_styles": [
        "input[placeholder='Exclude styles']",
        "input[placeholder*='Exclude' i]",
    ],
    "song_title": [
        "input[placeholder='Song Title (Optional)']",
        "input[placeholder*='Song Title' i]",
    ],
}

# Bộ nhớ kết quả kiểm tra gần nhất (profile dùng chung nên lưu theo project_id
# và cả khoá -1 làm bản chung, để router đọc ra hiển thị).
_LAST_CHECK: dict[int, dict] = {}


def get_last_check(project_id: int) -> Optional[dict]:
    return _LAST_CHECK.get(project_id) or _LAST_CHECK.get(-1)


def _store(project_id: int, result: dict) -> None:
    _LAST_CHECK[project_id] = result
    _LAST_CHECK[-1] = result


def _match_visible(page, sel: str) -> bool:
    try:
        loc = page.locator(sel).first
        return loc.count() > 0 and loc.is_visible()
    except Exception:
        return False


def _first_match(page, sels: list[str]) -> Optional[str]:
    """Trả selector đầu tiên khớp 1 phần tử đang hiển thị, hoặc None."""
    for sel in sels:
        if _match_visible(page, sel):
            return sel
    return None


def _dedup(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in seq:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _best_effort_reveal(page, selectors: dict) -> None:
    """Best-effort: chọn tab Advanced + mở More Options để lộ styles_box,
    exclude_styles, duration... KHÔNG raise nếu thất bại."""
    adv = query_first(page, selectors.get("mode_advanced_tab", []),
                      timeout_ms=4000)
    if adv is not None:
        try:
            adv.click()
            page.wait_for_timeout(600)
        except Exception:
            pass
    more = query_first(page, selectors.get("more_options", []), timeout_ms=2500)
    if more is not None:
        try:
            more.click()
            page.wait_for_timeout(500)
        except Exception:
            pass


def is_logged_in(page, selectors: dict, timeout_ms: int = 6000) -> bool:
    """Đăng nhập rồi nếu ĐANG Ở /create, thấy 1 trong các mốc chính của form
    tạo bài và KHÔNG có dấu hiệu đăng xuất. Trang chủ khi đăng xuất cũng có nút
    «Create»/«Advanced» → nếu chỉ dò form sẽ tưởng đã đăng nhập rồi kiểm/"heal"
    selector trên nhầm trang."""
    from .suno_driver import suno_logged_out
    if "/create" not in (page.url or "") or suno_logged_out(page):
        return False
    probes = (selectors.get("create_button", [])
              + selectors.get("styles_box", [])
              + selectors.get("mode_advanced_tab", []))
    return (query_first(page, probes, timeout_ms=timeout_ms) is not None
            and not suno_logged_out(page))


def run_selector_check(page, selectors: dict) -> dict:
    """Dò các nhóm selector page-level; heal nhóm hỏng bằng selector dự phòng.
    Trả dict: groups[], discovered{}, summary{}, checked_at."""
    _best_effort_reveal(page, selectors)

    groups: list[dict] = []
    discovered: dict[str, list[str]] = {}

    for name in PAGE_LEVEL:
        current = selectors.get(name, [])
        matched = _first_match(page, current)
        if matched:
            groups.append({"name": name, "status": "ok",
                           "matched": matched, "kind": "page"})
            continue
        healed = _first_match(page, HEAL_CANDIDATES.get(name, []))
        if healed:
            discovered[name] = _dedup([healed] + current)
            groups.append({"name": name, "status": "healed",
                           "matched": healed, "kind": "page"})
        else:
            groups.append({"name": name, "status": "missing",
                           "matched": None, "kind": "page"})

    for name in CONDITIONAL:
        current = selectors.get(name, [])
        matched = _first_match(page, current)
        if matched:
            groups.append({"name": name, "status": "ok",
                           "matched": matched, "kind": "conditional"})
            continue
        healed = _first_match(page, HEAL_CANDIDATES.get(name, []))
        if healed:
            discovered[name] = _dedup([healed] + current)
            groups.append({"name": name, "status": "healed",
                           "matched": healed, "kind": "conditional"})
        else:
            # Không hiện ở trạng thái probe → thông tin, không phải lỗi.
            groups.append({"name": name, "status": "conditional",
                           "matched": None, "kind": "conditional"})

    for name in INTERACTIVE:
        if name in selectors:
            groups.append({"name": name, "status": "skipped",
                           "matched": None, "kind": "interactive"})

    page_groups = [g for g in groups if g["kind"] == "page"]
    return {
        "groups": groups,
        "discovered": discovered,
        "summary": {
            "page_ok": sum(1 for g in page_groups
                           if g["status"] in ("ok", "healed")),
            "page_total": len(page_groups),
            "healed": [g["name"] for g in groups if g["status"] == "healed"],
            "missing": [g["name"] for g in groups if g["status"] == "missing"],
        },
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _summary_text(result: dict) -> str:
    s = result.get("summary", {})
    parts = [f"Selector page-level OK {s.get('page_ok', 0)}/"
             f"{s.get('page_total', 0)}"]
    if s.get("healed"):
        parts.append(f"tự sửa+lưu: {', '.join(s['healed'])}")
    if s.get("missing"):
        parts.append(f"cần sửa tay: {', '.join(s['missing'])}")
    return " | ".join(parts)


def run_login_and_check(project_id: int, progress_cb: Callable[[str, float], None],
                        hold_seconds: int = 900) -> dict:
    """Mở trình duyệt Suno cho người dùng đăng nhập, rồi kiểm/heal selector và
    giữ cửa sổ mở tới khi bấm 'Đóng trình duyệt' (huỷ hợp tác) hoặc hết
    hold_seconds. KHÔNG tạo/tải gì → không tốn credit."""
    from .job_manager import JobCancelled

    selectors = vconfig.get_selectors("suno")
    progress_cb("Đang mở trình duyệt Suno (profile riêng)…", 5.0)

    with BrowserSession(headless=False,
                        profile_dir=vconfig.SUNO_PROFILE_DIR) as bs:
        page = bs.new_page()
        try:
            page.goto(vconfig.SUNO_URL, wait_until="domcontentloaded")
        except Exception:
            pass
        progress_cb("Đã mở suno.com/create. Nếu chưa đăng nhập, hãy đăng nhập "
                    "trong cửa sổ vừa mở (tool KHÔNG tự nhập mật khẩu).", 12.0)

        # Chờ đăng nhập tối đa ~5 phút, vẫn phản hồi nút Đóng (progress_cb ném
        # JobCancelled khi bị huỷ). Người dùng đăng nhập xong thì check chạy.
        from .suno_driver import suno_logged_out
        logged_in = False
        wait_deadline = time.time() + 300
        stuck_since = time.time()
        try:
            while time.time() < wait_deadline:
                if is_logged_in(page, selectors, timeout_ms=1500):
                    logged_in = True
                    break
                url = page.url or ""
                # Đăng nhập xong Suno hay đưa về trang chủ; session-recovery có
                # lúc treo → sau 20s mà không ở luồng đăng nhập thì tự về /create.
                if ("/create" in url or "/auth/" in url or "sign-in" in url
                        or suno_logged_out(page)) and "session-recovery" not in url:
                    stuck_since = time.time()
                elif time.time() - stuck_since > 20:
                    try:
                        page.goto(vconfig.SUNO_URL, wait_until="domcontentloaded",
                                  timeout=45000)
                    except Exception:
                        pass
                    stuck_since = time.time()
                progress_cb("Đang chờ đăng nhập Suno trong cửa sổ trình duyệt…",
                            30.0)
                page.wait_for_timeout(1500)
        except JobCancelled:
            progress_cb("Đang đóng trình duyệt…", 95.0)
            return {"selector_check": get_last_check(project_id),
                    "logged_in": False, "closed_before_check": True}

        progress_cb("Đang kiểm tra selector trên giao diện Suno hiện tại…", 55.0)
        result = run_selector_check(page, selectors)
        result["logged_in"] = logged_in
        _store(project_id, result)

        discovered = result.get("discovered") or {}
        if discovered:
            try:
                vconfig.save_selector_overrides("suno", discovered)
                result["saved_overrides"] = sorted(discovered.keys())
                progress_cb(f"Đã tự lưu {len(discovered)} selector mới vào "
                            "video_overrides.json.", 70.0)
            except Exception as e:                       # noqa: BLE001
                result["save_error"] = f"{type(e).__name__}: {e}"
            _store(project_id, result)

        summary = _summary_text(result)
        progress_cb(summary + " — Cửa sổ vẫn mở, bấm 'Đóng trình duyệt' khi xong.",
                    82.0)

        # Giữ mở cho người dùng kiểm tra thủ công tới khi Đóng (huỷ) hoặc hết giờ.
        deadline = time.time() + hold_seconds
        try:
            while time.time() < deadline:
                progress_cb(summary + " — cửa sổ đang mở để bạn kiểm tra.", 85.0)
                page.wait_for_timeout(1500)
        except JobCancelled:
            progress_cb("Đang đóng trình duyệt…", 95.0)

        return {"selector_check": result, "logged_in": logged_in}
