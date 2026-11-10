"""
cover/suno_cover.py — Điều khiển Suno để làm 1 bản COVER (đợt B, tab ② Suno Cover).

Kế thừa SunoDriver của tool cũ (đăng nhập, model, Max Mode, thanh trượt, chờ
captcha, tải WAV qua Studio) và thêm các bước riêng của cover:
  đính file đã chỉnh tốc độ → Open in Editor → đặt Tempo 32/35 → Save as New
  Song → Remix → Cover → tên bài, lời, giọng Nam/Nữ, Audio Influence.

Nguyên tắc an toàn (giữ như tool cũ):
  • KHÔNG bấm Create khi chưa XÁC NHẬN form đang ở chế độ Cover có file gốc
    (thấy thanh «Audio Influence») — tránh tạo bài sai, tốn credit.
  • Hộp thoại điều khoản / xác nhận quyền với bản ghi âm: tool KHÔNG tự bấm đồng
    ý — đưa cửa sổ Suno lên, báo người dùng tự bấm, rồi chờ.
  • Captcha: người dùng tự giải (SunoDriver.wait_for_new_songs).
  • Suno từ chối file (bản quyền) → SunoCoverRejected; tool KHÔNG tìm cách lách.

Giao diện Cover của Suno chưa đo LIVE được (cần tài khoản của người dùng) → mọi
bước đều dò nhiều cách và khi không chắc thì DỪNG kèm ảnh chụp trong
ban_cover/_suno/_debug. Nút «Chạy thử» điền form rồi dừng TRƯỚC Create.
"""
from __future__ import annotations

import json
import re
import unicodedata
import time
from pathlib import Path
from typing import Callable, Optional

from backend.video.browser_base import query_first
from backend.video.suno_driver import SunoDriver, SunoError, SunoUIChanged

LYRICS_LIMIT = 5000

# Thông số người dùng chốt (file Word «Workflow làm nhạc cover youtube»).
COVER_MODEL = "v6"
COVER_SLIDERS = {"weirdness": 10, "style_influence": 75,
                 # Suno v6: Variety cập nhật lại style prompt. Để 0 nhằm giữ
                 # nguyên instrument/performance locks của tool.
                 "variety": 0,            # 0–4: Off/Normal/High/Extra/Max
                 "audio_influence": 50}

EDITOR_TEMPO_SLOW = 32
EDITOR_TEMPO_FAST = 35


def _genre_key(music_style: str, cover_style: str, description: str) -> str:
    """Thể loại dùng cho Styles. music_style người dùng chọn LUÔN quyết định;
    chỉ khi nó trống/lạ mới xét cover_style rồi mô tả (mô tả là ý tưởng hình
    ảnh — chữ «ballad» trong đó không được lấn át Deep House đã chọn)."""
    from .genre_catalog import genre_key
    return genre_key(music_style, cover_style, description)


def lyrics_lock(gender: str = "", lyrics_lang: str = "", voice_word: str = "vocal") -> str:
    """Khoá giọng + lời: hát đúng ngôn ngữ của LỜI GỐC (không phải dòng nhạc)."""
    voice = (f"male {voice_word}" if gender == "male" else
             f"female {voice_word}" if gender == "female" else
             f"male-female duet {voice_word}s with clearly assigned alternating parts"
             if gender == "duet" else f"natural {voice_word}")
    lang = (lyrics_lang or "").strip()
    sung = (f"sung in {lang} with clear {lang} pronunciation" if lang else
            "sung in the original language of the supplied lyrics with clear pronunciation")
    return (
        f"{voice}, {sung}, bright clear full-bodied lead vocal upfront and centered about "
        "3 dB above instrumental, crisp diction, continuous dominant lead singing, "
        "instruments below voice, sparse fills only between phrases, no instrumental solos "
        "or long breaks, featured parts follow the existing groove with no off-grid hits "
        "or added percussion, preserve the supplied "
        "lyrics exactly, never translate, no lyrics in any other language"
    )


def cover_styles_for_project(music_style: str = "", description: str = "",
                             cover_style: str = "", gender: str = "",
                             music_line: str = "", lyrics_lang: str = "") -> str:
    """Fallback Styles khi API AI không dùng được — bám Thể loại + Dòng nhạc."""
    from .genre_catalog import genre_prompt_core
    from .music_line import line_profile
    line = line_profile(music_line)
    core = genre_prompt_core(music_style, cover_style, description)
    head, comma, rest = core.partition(",")
    core = f"{head}, {line['prompt_modifier']}{comma}{rest}"
    return f"{core}, {lyrics_lock(gender, lyrics_lang)}"


def editor_tempo_for_genre(*labels: str) -> int:
    """BPM dùng trong Suno Editor theo quy ước workflow của người dùng.

    Remix/House/Rock/Vietmix được ưu tiên 35 nếu một mô tả chứa cả từ khóa
    nhanh và chậm. Nhạc nhẹ (Ballad, Pop, R&B, Jazz, Blues, Lofi) và những thể
    loại còn lại dùng 32.
    """
    raw = " ".join(x or "" for x in labels).lower()
    folded = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    folded = re.sub(r"[^a-z0-9&]+", " ", folded)
    if re.search(r"\b(remix|house|deep house|deephouse|vietmix|viet mix|rock)\b", folded):
        return EDITOR_TEMPO_FAST
    # blue là cách người dùng gọi tắt Blues; rnb bao gồm R&B sau khi chuẩn hóa.
    if re.search(r"\b(lofi|lo fi|r&b|rnb|jazz|blue|blues|pop|ballad)\b", folded):
        return EDITOR_TEMPO_SLOW
    return EDITOR_TEMPO_SLOW


class SunoCoverRejected(SunoError):
    """Suno từ chối dùng file bài gốc (nhận diện bản quyền) — xử lý tay."""


# Thông báo Suno từ chối bản ghi âm. Chỉ đọc toast/hộp thoại đang hiện.
REJECT_RE = re.compile(
    r"copyright|protected (content|material|work|audio)|"
    r"match(es)? (an? )?(existing|known|commercial)|"
    r"can(no|')t (use|upload|process) this (audio|song|file|track)|"
    r"(not|n't) (allowed|permitted|able) to (use|upload) this|"
    r"rights ?holder|bản quyền", re.I)
# Hộp thoại đòi NGƯỜI DÙNG đồng ý điều khoản / xác nhận có quyền với bản ghi âm.
TERMS_RE = re.compile(
    r"terms of (service|use)|privacy policy|i (agree|accept|confirm|certify)|"
    r"(you|i) (own|have|hold) (all |the )*(necessary |legal )*rights?|"
    r"rights to (this|the|use)|by (continuing|uploading|clicking)|"
    r"điều khoản|đồng ý", re.I)
# Nút «làm tiếp» an toàn trong hộp thoại cắt đoạn/tải lên (KHÔNG có agree/accept).
_NEXT_BUTTONS = ("Continue", "Save", "Done", "Next", "Upload", "Use audio",
                 "Use Audio", "Use clip", "Confirm crop", "Crop")
_UPLOAD_ITEMS = [
    "[role=menu] [role=menuitem]:has-text('Upload')",
    "[role=menu] div.hxc-menu-item:has-text('Upload')",
    "[role=dialog] button:has-text('Upload')",
    "[role=menuitem]:has-text('Upload')",
    "button:has-text('Upload audio')",
    "button:text-is('Upload')",
]
_AUDIO_BUTTONS = [
    "button[aria-label^='Add audio']",
    "[aria-label^='Add audio']",
    "button:text-is('Audio')",
    "button:has-text('+ Audio')",
]

_OPEN_EDITOR = [
    "button:text-is('Open in Editor')",
    "[role=button]:text-is('Open in Editor')",
    "[role=menuitem]:has-text('Open in Editor')",
    "div.hxc-menu-item:has-text('Open in Editor')",
    "div.context-menu-item:has-text('Open in Editor')",
    "button.hxc-btn-base:has-text('Open in Editor')",
    "button:has-text('Open in editor')",
]
_EDITOR_TEMPO = [
    "input[aria-label*='Tempo' i]",
    "input[aria-label*='BPM' i]",
    "input[name*='tempo' i]",
    "input[name*='bpm' i]",
    "input[placeholder*='Tempo' i]",
    "input[placeholder*='BPM' i]",
    "[role=spinbutton][aria-label*='Tempo' i]",
    "[role=spinbutton][aria-label*='BPM' i]",
]
_SAVE_AS_NEW = [
    "button:text-is('Save as New Song')",
    "[role=button]:text-is('Save as New Song')",
    "[role=menuitem]:has-text('Save as New Song')",
    "button:has-text('Save as new song')",
]

_JS_NOTICES = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const pick = sel => [...document.querySelectorAll(sel)].filter(vis)
    .map(e => (e.innerText || '').trim().replace(/\s+/g, ' ')).filter(Boolean);
  return {dialogs: pick("[role=dialog],[role=alertdialog]"),
          toasts: pick("[data-sonner-toast],[role=alert],[class*=toast],[class*=Toast]")};
}"""

# Bảng cam Suno sau Create (LIVE 2026-11-09): «Couldn't generate that. Your lyrics
# contain copyrighted material. Please change it and try again.» — quét rộng hơn
# _JS_NOTICES (role=status, khung thông báo bất kỳ có đúng câu này).
_JS_CREATE_NOTICES = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const norm = e => (e.innerText || '').trim().replace(/\s+/g, ' ');
  const out = [...document.querySelectorAll(
      "[data-sonner-toast],[role=alert],[role=status],[class*=toast],[class*=Toast]," +
      "[class*=notification],[class*=Notification]")].filter(vis).map(norm)
    .filter(t => t && t.length < 600);
  const re = /couldn.?t generate that|contain(s)? copyrighted material/i;
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n = 0;
  while (w.nextNode() && n < 5) {
    if (!re.test(w.currentNode.nodeValue || '')) continue;
    let e = w.currentNode.parentElement;
    for (let i = 0; i < 3 && e && e.parentElement && norm(e.parentElement).length < 400; i++)
      e = e.parentElement;
    if (e && vis(e)) { out.push(norm(e)); n++; }
  }
  return out;
}"""

# Đóng bảng cam (nút X / Close / Dismiss trong chính thông báo). Không bấm gì khác.
_JS_CLOSE_CREATE_NOTICE = r"""() => {
  const re = /couldn.?t generate|copyright/i;
  let closed = 0;
  for (const t of document.querySelectorAll(
      "[data-sonner-toast],[role=alert],[role=status],[class*=toast],[class*=Toast]")) {
    if (!re.test(t.innerText || '')) continue;
    const b = [...t.querySelectorAll('button,[role=button]')].find(x =>
      /close|dismiss|đóng/i.test((x.getAttribute('aria-label') || '') + ' ' + (x.innerText || ''))
      || ['×', '✕', 'x'].includes((x.innerText || '').trim().toLowerCase()));
    if (b) { b.click(); closed++; }
  }
  return closed;
}"""

# Hộp thoại đang hiện trên cùng: chữ + các nút (để chọn nút «làm tiếp»).
_JS_TOP_DIALOG = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const ds = [...document.querySelectorAll("[role=dialog],[role=alertdialog]")].filter(vis);
  if (!ds.length) return null;
  const d = ds[ds.length - 1];
  document.querySelectorAll('[data-tool-dlg]').forEach(e => e.removeAttribute('data-tool-dlg'));
  const btns = [...d.querySelectorAll('button')].filter(vis).map((b, i) => {
    b.setAttribute('data-tool-dlg', String(i));
    return {i, text: (b.innerText || b.getAttribute('aria-label') || '').trim(),
            enabled: !b.disabled && b.getAttribute('aria-disabled') !== 'true'};
  });
  return {text: (d.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 600), buttons: btns};
}"""

# Lựa chọn «Cover» đang hiện (tab/nút/mục menu). Đánh dấu để bấm bằng chuột thật.
_JS_MARK_COVER = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const txt = e => (e.innerText || e.textContent || '').trim();
  const on = e => {
    const a = e.getAttribute('aria-selected') ?? e.getAttribute('aria-checked')
          ?? e.getAttribute('data-selected') ?? e.getAttribute('aria-pressed')
          ?? e.getAttribute('data-state');
    return a !== null && /^(true|on|checked|active)$/i.test(a);
  };
  document.querySelectorAll('[data-tool-cover]').forEach(e => e.removeAttribute('data-tool-cover'));
  const sel = "button,[role=tab],[role=radio],[role=menuitem],[role=menuitemradio],[role=option]," +
              "div.hxc-menu-item,div.context-menu-item";
  const c = [...document.querySelectorAll(sel)].filter(vis)
    .filter(e => /^cover$/i.test(txt(e)) || /^cover\b/i.test(e.getAttribute('aria-label') || ''))
    .filter(e => !e.closest("a[href*='/song/']"));
  if (!c.length) return null;
  const pick = c.find(on) || c[0];
  pick.setAttribute('data-tool-cover', '1');
  return {selected: on(pick), count: c.length};
}"""

# Hàng 2 nút (vd «Vocal Gender»: Male / Female). want = chữ của nút cần bật.
_JS_PAIR_ROW = r"""([label, a, b, want]) => {
  const vis = e => {
    let r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    let left=r.left, top=r.top, right=r.right, bottom=r.bottom;
    for (let p=e.parentElement; p && right>left && bottom>top; p=p.parentElement) {
      const s=getComputedStyle(p), o=s.overflow+' '+s.overflowX+' '+s.overflowY;
      if (/hidden|clip/.test(o)) {
        const q=p.getBoundingClientRect();
        left=Math.max(left,q.left); top=Math.max(top,q.top);
        right=Math.min(right,q.right); bottom=Math.min(bottom,q.bottom);
      }
    }
    return right-left > 2 && bottom-top > 2;
  };
  const txt = e => (e.innerText || e.textContent || '').trim();
  const state = e => {
    const v = e.getAttribute('data-selected') ?? e.getAttribute('aria-pressed')
          ?? e.getAttribute('aria-checked') ?? e.getAttribute('data-state');
    if (v !== null) return /^(true|on|checked|active|selected)$/i.test(v);
    const input = e.matches('input') ? e : e.querySelector('input[type=radio],input[type=checkbox]');
    if (input) return !!input.checked;
    const cls = String(e.className || '');
    if (/(^|\s)(is-selected|selected|active|checked)(\s|$)|variant-(primary|accent)/i.test(cls))
      return true;
    // Suno 2026-11 không còn công khai trạng thái của cặp nút qua DOM.
    return null;
  };
  document.querySelectorAll('[data-tool-pair]').forEach(e => e.removeAttribute('data-tool-pair'));
  for (const l of document.querySelectorAll('span,div,p,label')) {
    if (l.children.length || txt(l) !== label || !vis(l)) continue;
    let row = l;
    for (let i = 0; i < 4 && row; i++) {
      row = row.parentElement;
      if (!row) break;
      const btns = [...row.querySelectorAll('button')].filter(vis);
      const x = btns.find(e => txt(e) === a), y = btns.find(e => txt(e) === b);
      if (x && y) {
        (want === a ? x : y).setAttribute('data-tool-pair', '1');
        const sx = state(x), sy = state(y);
        return {[a]: sx === true, [b]: sy === true,
                verifiable: sx !== null || sy !== null,
                desiredDisabled: !!(want === a ? x : y).disabled,
                desiredHtml: (want === a ? x : y).outerHTML.slice(0, 500)};
      }
    }
  }
  return null;
}"""

# Nút/menu đang hiện (để chẩn đoán khi không tìm ra chế độ Cover).
_JS_VISIBLE_CONTROLS = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const out = [];
  for (const e of document.querySelectorAll("button,[role=tab],[role=menuitem],[role=slider]")) {
    if (!vis(e)) continue;
    const t = ((e.innerText || '').trim() || e.getAttribute('aria-label') || '').replace(/\s+/g, ' ');
    if (t && t.length <= 40 && !out.includes(t)) out.push(t);
    if (out.length >= 60) break;
  }
  return out;
}"""

_JS_MARK_SONG_TITLE = r"""() => {
  const vis = e => { const r=e.getBoundingClientRect(); return r.width>0 && r.height>0; };
  document.querySelectorAll('[data-tool-song-title]')
    .forEach(e => e.removeAttribute('data-tool-song-title'));
  const direct=[...document.querySelectorAll('input,textarea,[role=textbox],[contenteditable=true]')]
    .find(e => /song\s*title/i.test((e.getAttribute('placeholder')||'')+' '+
      (e.getAttribute('aria-label')||'')+' '+(e.getAttribute('name')||'')));
  if (direct) { direct.setAttribute('data-tool-song-title','1'); return true; }
  const labels=[...document.querySelectorAll('label,span,div,p')]
    .filter(e => /song\s*title/i.test((e.innerText||e.textContent||'').trim()));
  for (const label of labels) {
    let row=label;
    for (let i=0;i<5 && row;i++,row=row.parentElement) {
      const field=row.querySelector('input,textarea,[role=textbox],[contenteditable=true]');
      if (field) { field.setAttribute('data-tool-song-title','1'); return true; }
    }
  }
  return false;
}"""

# Upload audio mới của Suno hiện có thể là một hàng chỉ ghi ``UPLOAD`` và KHÔNG
# có link /song/<uuid>. Đánh dấu nút ⋯ trong đúng hàng bằng tên file để vào
# Edit → Open in Editor mà không cần đoán song id.
_JS_MARK_UPLOAD_MORE = r"""(fileStem) => {
  const vis = e => { const r=e.getBoundingClientRect(); return r.width>0 && r.height>0; };
  // Suno keeps the Vietnamese filename in the row.  The previous ASCII-only
  // normaliser removed most accented letters ("Người Ướt Áo" became almost
  // empty), so an existing UPLOAD row could not be matched and the tool kept
  // uploading duplicates.  Strip combining marks first and keep letters/
  // numbers from every language before comparing.
  const norm = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  document.querySelectorAll('[data-tool-upload-more]')
    .forEach(e => e.removeAttribute('data-tool-upload-more'));
  const want = norm(fileStem).slice(0, 45);
  let best = null, bestArea = Infinity;
  // LIVE Suno changed UPLOAD from a leaf <span> to a badge containing an icon.
  // Work from each visible More button and climb to its compact song row, so
  // both DOM shapes are supported.
  const sel = "button[aria-label='More options'],button[aria-label='More']";
  const mores = [...document.querySelectorAll(sel)].filter(vis);
  for (const more of mores) {
    // Chỉ xét hàng RIÊNG của nút này: dừng trước tổ tiên chứa thêm nút ⋯ của
    // hàng khác. (LIVE 2026-11-08: hàng «… (#148) (Edit)» nằm sát một hàng có
    // chữ UPLOAD → khối gộp hai hàng khớp cả tên lẫn UPLOAD → bấm nhầm ⋯.)
    let row = more.parentElement;
    for (let depth=0; depth<10 && row; depth++, row=row.parentElement) {
      if ([...row.querySelectorAll(sel)].some(b => b !== more && vis(b))) break;
      const text = norm(row.innerText || row.textContent || '');
      const r = row.getBoundingClientRect(), area = r.width * r.height;
      if (!/(^| )upload( |$)/.test(text)) continue;
      const titleMatch = !want || text.includes(want) ||
        want.includes(text.slice(0, Math.min(32, text.length)));
      if (titleMatch && r.height > 25 && r.height < 240 && area < bestArea) {
        best = more; bestArea = area;
      }
    }
  }
  if (!best) return false;
  best.setAttribute('data-tool-upload-more', '1');
  return true;
}"""

# Đánh dấu mục menu đang hiện có dòng đầu ĐÚNG bằng nhãn (không phân biệt hoa
# thường). Dòng sau là huy hiệu «Pro»/«New» nên chỉ so dòng đầu.
_JS_MARK_MENU_ITEM = r"""(label) => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const head = e => ((e.innerText || e.textContent || '').split('\n')[0] || '')
    .replace(/\s+/g, ' ').trim().toLowerCase();
  document.querySelectorAll('[data-tool-menu-item]')
    .forEach(e => e.removeAttribute('data-tool-menu-item'));
  const want = (label || '').toLowerCase();
  const hit = [...document.querySelectorAll('[role=menu] [role=menuitem]')]
    .filter(vis).find(e => head(e) === want);
  if (!hit) return false;
  hit.setAttribute('data-tool-menu-item', want);
  return true;
}"""

_JS_OPEN_MENU_ITEMS = r"""() => [...document.querySelectorAll('[role=menu] [role=menuitem]')]
  .map(e => (e.innerText || '').split('\n')[0].trim()).filter(Boolean).slice(0, 40)"""

# Dán văn bản vào ô contenteditable (Lexical) bằng sự kiện paste — giống Ctrl+V.
_JS_PASTE_TEXT = r"""(el, text) => { el.focus();
  const dt = new DataTransfer(); dt.setData('text/plain', text);
  el.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true,
                                                cancelable: true}));
  return true; }"""

# [href, chữ] của mọi link bài theo thứ tự DOM (danh sách sắp xếp New → mới trước).
_JS_SONG_LINKS = r"""() => [...document.querySelectorAll("a[href*='/song/']")]
  .map(a => [a.getAttribute('href') || '', (a.innerText || a.textContent || '').trim()])"""


def _fold_title(text: str) -> str:
    """So tên bài: NFC, chữ thường, gộp khoảng trắng."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "")).strip().casefold()


# Một số bản Suno không đặt aria-label cho ô Tempo. Đánh dấu input gần nhãn
# Tempo/BPM nhất để Playwright vẫn thao tác được, nhưng không đoán ô number khác.
_JS_MARK_EDITOR_TEMPO = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const txt = e => (e.innerText || e.textContent || '').trim();
  document.querySelectorAll('[data-tool-editor-tempo]').forEach(
    e => e.removeAttribute('data-tool-editor-tempo'));
  const labels = [...document.querySelectorAll('label,span,div,p')].filter(vis)
    .filter(e => e.children.length === 0 && /^(tempo|bpm)$/i.test(txt(e)));
  for (const label of labels) {
    let row = label;
    for (let i = 0; i < 5 && row; i++, row = row.parentElement) {
      const input = [...row.querySelectorAll(
        "input,[role=spinbutton],[role=slider],[contenteditable=true]")].find(vis);
      if (input) {
        input.setAttribute('data-tool-editor-tempo', '1');
        return true;
      }
    }
  }
  return false;
}"""

_JS_MARK_EDITOR_TEMPO_INPUT = r"""() => {
  const vis = e => { const r=e.getBoundingClientRect(); return r.width>0 && r.height>0; };
  document.querySelectorAll('[data-tool-editor-tempo-input]')
    .forEach(e => e.removeAttribute('data-tool-editor-tempo-input'));
  const inputs=[...document.querySelectorAll('input')].filter(e => {
    if (!vis(e) || e.disabled || e.getAttribute('aria-disabled') === 'true') return false;
    const r=e.getBoundingClientRect(), n=Number(e.value);
    return Number.isFinite(n) && n>=20 && n<=300 && r.y>innerHeight*.65 &&
           r.x>innerWidth*.45 && r.x<innerWidth*.85;
  });
  if (inputs.length !== 1) return null;
  inputs[0].setAttribute('data-tool-editor-tempo-input','1');
  return {value:Number(inputs[0].value), type:inputs[0].type || ''};
}"""

# Editor Beta có biến thể hiển thị BPM bằng ``input[type=number] disabled``.
# Muốn chỉnh phải bấm nút bánh răng ngay bên trái trước; sau đó Suno mới bật
# control Tempo thật. Không click spinner native của input disabled.
_JS_MARK_EDITOR_TEMPO_GEAR = r"""() => {
  const vis=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0};
  document.querySelectorAll('[data-tool-tempo-gear]')
    .forEach(e=>e.removeAttribute('data-tool-tempo-gear'));
  const inputs=[...document.querySelectorAll('input[type=number]:disabled')].filter(e=>{
    if(!vis(e)) return false; const n=Number(e.value);
    return Number.isFinite(n)&&n>=20&&n<=300;
  });
  for(const input of inputs){
    const ir=input.getBoundingClientRect(), icy=ir.y+ir.height/2;
    const buttons=[...document.querySelectorAll('button,[role=button]')].filter(b=>{
      if(!vis(b)) return false; const r=b.getBoundingClientRect(), cx=r.x+r.width/2;
      return cx<ir.x && ir.x-cx<95 &&
             Math.abs((r.y+r.height/2)-icy)<Math.max(32,ir.height*1.5);
    }).sort((a,b)=>b.getBoundingClientRect().x-a.getBoundingClientRect().x);
    if(buttons[0]){
      buttons[0].setAttribute('data-tool-tempo-gear','1');
      return {value:Number(input.value), distance:ir.x-(buttons[0].getBoundingClientRect().x+
        buttons[0].getBoundingClientRect().width/2)};
    }
  }
  return null;
}"""

# Song Editor Beta (LIVE 2026-10-28) hiển thị BPM ở thanh dưới dạng
# "[-] 91 [+]", không có input/aria-label Tempo. Bắt buộc tìm thấy cả nút tăng
# lẫn giảm trong cùng cụm. Suno có lúc dùng button chỉ chứa SVG nên ngoài nhãn
# còn nhận diện hai nút nằm sát bên trái/phải con số BPM.
_JS_MARK_EDITOR_TEMPO_STEPPER = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const text = e => (e.innerText || e.textContent || '').trim();
  const valueText = e => e.matches('input') ? String(e.value || '').trim() : text(e);
  const label = e => (e.getAttribute('aria-label') || e.getAttribute('title') || text(e)).trim();
  const minus = s => /^(?:-|−|–)$/.test(s) || /(?:decrease|lower|slower|tempo down|bpm down)/i.test(s);
  const plus = s => /^\+$/.test(s) || /(?:increase|raise|faster|tempo up|bpm up)/i.test(s);
  document.querySelectorAll('[data-tool-tempo-value],[data-tool-tempo-step]')
    .forEach(e => { e.removeAttribute('data-tool-tempo-value'); e.removeAttribute('data-tool-tempo-step'); });
  const leaves = [...document.querySelectorAll('span,div,p,button,input')].filter(e => {
    if (!vis(e) || (!e.matches('input') && e.children.length)) return false;
    const raw=valueText(e), n=Number(raw);
    return /^\d{2,3}$/.test(raw) && n >= 20 && n <= 300;
  });
  for (const value of leaves) {
    const vr = value.getBoundingClientRect(), vc = vr.x + vr.width / 2;
    let box = value.parentElement;
    for (let depth = 0; depth < 6 && box; depth++, box = box.parentElement) {
      const controls = [...box.querySelectorAll('button,[role=button],span,div')]
        .filter(e => vis(e) && (e.children.length === 0 || e.matches('button,[role=button]')));
      let down = controls.find(e => minus(label(e)));
      let up = controls.find(e => plus(label(e)));
      // Icon-only buttons: infer direction only when both buttons are very near
      // the BPM value and vertically overlap it.
      if ((!down || !up) && !value.matches('input')) {
        const near = controls.filter(e => {
          if (!e.matches('button,[role=button]')) return false;
          const r=e.getBoundingClientRect(), cy=r.y+r.height/2;
          return Math.abs(cy-(vr.y+vr.height/2)) < Math.max(30, vr.height*1.5) &&
                 Math.abs((r.x+r.width/2)-vc) < 130;
        });
        const left = near.filter(e => e.getBoundingClientRect().x < vr.x)
          .sort((a,b) => b.getBoundingClientRect().x-a.getBoundingClientRect().x)[0];
        const right = near.filter(e => e.getBoundingClientRect().x > vr.right)
          .sort((a,b) => a.getBoundingClientRect().x-b.getBoundingClientRect().x)[0];
        down = down || left; up = up || right;
      }
      if (down && up) {
        value.setAttribute('data-tool-tempo-value', '1');
        down.setAttribute('data-tool-tempo-step', 'down');
        up.setAttribute('data-tool-tempo-step', 'up');
        return {value: Number(valueText(value)), depth, down: label(down), up: label(up),
                source: value.matches('input') ? 'disabled-display-input' : 'text'};
      }
    }
  }
  return null;
}"""


class CoverDriver(SunoDriver):
    SLIDERS = {**SunoDriver.SLIDERS, "audio_influence": "Audio Influence"}

    def __init__(self, page, dry_run: bool = True, selectors: Optional[dict] = None,
                 log: Optional[Callable[[str], None]] = None,
                 on_human: Optional[Callable[[str], None]] = None):
        super().__init__(page, dry_run=dry_run, selectors=selectors, log=log)
        self.on_human = on_human
        # Số Filters (N) mặc định của Suno không xoá được (Hide Disliked/Stems/
        # Clips from Edit Mode) — đã thử 1 lần thì lần sau bỏ qua (~7 giây/lần).
        self._filters_stuck_at: Optional[int] = None

    # ── tiện ích ─────────────────────────────────────────────────
    def _notices(self) -> dict:
        try:
            return self.page.evaluate(_JS_NOTICES) or {}
        except Exception:
            return {}

    def rejection_text(self) -> str:
        """Chữ của toast/hộp thoại Suno báo từ chối file (bản quyền), '' nếu không có.
        Hộp thoại điều khoản (có nhắc 'copyright') KHÔNG tính là từ chối."""
        n = self._notices()
        for t in (n.get("toasts") or []) + (n.get("dialogs") or []):
            if REJECT_RE.search(t) and not TERMS_RE.search(t):
                return t[:300]
        return ""

    def create_rejection_text(self) -> str:
        """Bảng cam Suno báo lyrics/audio bản quyền SAU KHI bấm Create, '' nếu không có."""
        try:
            texts = self.page.evaluate(_JS_CREATE_NOTICES) or []
        except Exception:
            texts = []
        for t in texts:
            if (REJECT_RE.search(t) or re.search(r"couldn.?t generate", t, re.I)) \
                    and not TERMS_RE.search(t):
                return t[:300]
        return self.rejection_text()

    def close_create_rejection(self, wait_sec: float = 20) -> bool:
        """Đóng/chờ bảng cam biến mất trước khi Create lại. True = đã hết."""
        end = time.time() + wait_sec
        while time.time() < end:
            if not self.create_rejection_text():
                return True
            try:
                self.page.evaluate(_JS_CLOSE_CREATE_NOTICE)
            except Exception:
                pass
            self._wait(1000)
        return not self.create_rejection_text()

    def check_rejection(self) -> None:
        why = self.rejection_text()
        if why:
            shot = self.save_debug("rejected")
            raise SunoCoverRejected(f"Suno từ chối bài gốc: «{why}»"
                                    + (f" (ảnh: {shot})" if shot else ""))

    def _top_dialog(self) -> Optional[dict]:
        try:
            return self.page.evaluate(_JS_TOP_DIALOG)
        except Exception:
            return None

    def _visible_controls(self) -> str:
        try:
            return ", ".join(self.page.evaluate(_JS_VISIBLE_CONTROLS) or [])[:900]
        except Exception:
            return ""

    def _ui_changed(self, msg: str, tag: str) -> SunoUIChanged:
        shot = self.save_debug(tag)
        seen = self._visible_controls()
        return SunoUIChanged(msg + (f" Nút đang thấy: {seen}." if seen else "")
                             + (f" (ảnh: {shot})" if shot else ""))

    def _save_editor_probe(self) -> Optional[str]:
        """Lưu DOM gọn của 40% dưới Editor để cập nhật selector khi Suno đổi UI."""
        if self.debug_dir is None:
            return None
        try:
            rows = self.page.evaluate(r"""() => {
              const vis = e => { const r=e.getBoundingClientRect(); return r.width>0 && r.height>0; };
              const out=[];
              for (const e of document.querySelectorAll('button,input,[role],span,div,p')) {
                if (!vis(e)) continue;
                const r=e.getBoundingClientRect();
                const text=(e.innerText||e.textContent||'').trim().replace(/\s+/g,' ');
                const aria=e.getAttribute('aria-label')||'', title=e.getAttribute('title')||'';
                const value=e.matches('input') ? e.value : '';
                const loading=/loading edit session|legacy editor/i.test(text+' '+aria+' '+title);
                if (!loading && (r.y < innerHeight*.72 || r.x < innerWidth*.42)) continue;
                if ((!text && !aria && !title && !value) || text.length>60 || r.width>500) continue;
                out.push({tag:e.tagName,text,aria,title,role:e.getAttribute('role')||'',
                  value,type:e.getAttribute('type')||'',
                  cls:String(e.className||'').slice(0,180),
                  x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height),
                  html:(e.outerHTML||'').slice(0,500)});
              }
              return {url:location.href,width:innerWidth,height:innerHeight,nodes:out};
            }""")
            folder = Path(self.debug_dir)
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"editor_dom_{time.strftime('%H%M%S')}.json"
            path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            return str(path)
        except Exception as exc:
            self._log(f"Không lưu được DOM Editor: {exc}")
            return None

    def _click(self, loc) -> None:
        if not self._real_click(loc):
            loc.click(timeout=4000)

    def _clear_library_filters(self) -> None:
        """Xoá Filters (N) để snapshot DOM nhìn thấy bài vừa Save."""
        def count() -> int:
            try:
                texts = self.page.locator("button").all_inner_texts()
            except Exception:
                return 0
            for text in texts:
                m = re.fullmatch(r"\s*Filters\s*\((\d+)\)\s*", text or "", re.I)
                if m:
                    return int(m.group(1))
            return 0

        current = count()
        if current <= 0:
            return
        if self._filters_stuck_at is not None and current == self._filters_stuck_at:
            return
        filters = query_first(self.page, [
            "button:has-text('Filters')", "[role=button]:has-text('Filters')",
        ], timeout_ms=2500)
        if filters is not None:
            try:
                self._click(filters)
                self._wait(500)
                clear = query_first(self.page, [
                    "button:text-is('Clear all')", "button:text-is('Reset all')",
                    "button:text-is('Clear filters')", "button:text-is('Reset filters')",
                    "[role=button]:text-is('Clear all')",
                    "[role=button]:text-is('Reset all')",
                ], timeout_ms=1500)
                if clear is not None:
                    self._click(clear)
                    self._wait(800)
            except Exception:
                pass
        current = count()
        for label in ("Liked", "Public", "Uploads"):
            if current <= 0:
                break
            chip = query_first(self.page, [
                f"button:text-is('{label}')", f"[role=button]:text-is('{label}')",
            ], timeout_ms=1000)
            if chip is None:
                continue
            before = current
            try:
                self._click(chip)
                self._wait(500)
                current = count()
                if current > before:       # chip vốn chưa bật → hoàn tác
                    self._click(chip)
                    self._wait(400)
                    current = count()
            except Exception:
                current = count()
        if current == 0:
            self._close_open_menus()
            self._log("Đã xoá bộ lọc thư viện Suno để nhận đúng ID bài mới.")
        else:
            try:
                rows = self.page.evaluate(r"""() => [...document.querySelectorAll('*')]
                  .filter(e => {
                    const r=e.getBoundingClientRect(), t=(e.innerText||'').trim();
                    return r.width>0 && r.height>0 && t.length>0 && t.length<160 &&
                      /filter|liked|public|upload|clear|reset/i.test(t);
                  }).map(e => ({tag:e.tagName,text:(e.innerText||'').trim(),
                    role:e.getAttribute('role')||'', aria:e.getAttribute('aria-label')||'',
                    pressed:e.getAttribute('aria-pressed')||'', state:e.getAttribute('data-state')||'',
                    html:(e.outerHTML||'').slice(0,900)})).slice(0,120)""")
                if not self.debug_dir:
                    raise RuntimeError("chưa đặt thư mục _debug")
                folder = Path(self.debug_dir)
                folder.mkdir(parents=True, exist_ok=True)
                probe = folder / f"filters_dom_{time.strftime('%H%M%S')}.json"
                probe.write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
                self.save_debug("filters_not_cleared")
                self._log(f"Đã lưu DOM Filters để chẩn đoán: {probe}.")
            except Exception as exc:
                self._log(f"Không lưu được DOM Filters: {exc}")
            # LIVE 2026-11-09: nhánh này từng để menu Filters MỞ → cú bấm Create
            # ngay sau đó chỉ đóng menu, Suno không nhận lệnh tạo bài.
            self._close_open_menus()
            self._filters_stuck_at = current
            self._log(f"Thư viện Suno còn Filters ({current}); sẽ tiếp tục dò ID "
                      "nhưng không nhận bài cũ làm bài mới.")

    def options_open(self) -> bool:
        try:
            # Playwright is_visible() vẫn trả true cho slider có kích thước nhưng
            # đang bị ancestor More Options (overflow:hidden) cắt hoàn toàn.
            return bool(self.page.evaluate(r"""() => {
              const e=document.querySelector("[role=slider][aria-label='Weirdness']");
              if (!e) return false;
              let r=e.getBoundingClientRect();
              if (r.width<=0 || r.height<=0) return false;
              let l=r.left, t=r.top, rr=r.right, b=r.bottom;
              for (let p=e.parentElement; p && rr>l && b>t; p=p.parentElement) {
                const s=getComputedStyle(p), o=s.overflow+' '+s.overflowX+' '+s.overflowY;
                if (/hidden|clip/.test(o)) {
                  const q=p.getBoundingClientRect();
                  l=Math.max(l,q.left); t=Math.max(t,q.top);
                  rr=Math.min(rr,q.right); b=Math.min(b,q.bottom);
                }
              }
              return rr-l>2 && b-t>2;
            }"""))
        except Exception:
            return False

    def ensure_more_options(self) -> None:
        """Mở More Options nếu đang đóng (bấm khi đang mở sẽ ĐÓNG lại)."""
        if self.options_open():
            return
        selectors = [
            "button:text-is('More Options')",
            "[role=button]:text-is('More Options')",
            "button:has-text('More Options')",
            "button:text-is('Advanced Options')",
            "[role=button]:text-is('Advanced Options')",
        ]
        # Header này nằm trong panel Create có scroll riêng.  Playwright coi nó
        # là không visible khi bị mép panel cắt, nên query_first() không trả về
        # dù node vẫn tồn tại. Chủ động cuộn từng ứng viên vào vùng nhìn thấy.
        loc = None
        for selector in selectors:
            candidates = self.page.locator(selector)
            try:
                count = min(candidates.count(), 6)
            except Exception:
                continue
            for i in range(count):
                candidate = candidates.nth(i)
                try:
                    candidate.scroll_into_view_if_needed(timeout=1500)
                    if candidate.is_visible():
                        loc = candidate
                        break
                except Exception:
                    continue
            if loc is not None:
                break
        if loc is None:
            self._log("Không thấy nút More Options rõ ràng.")
            return
        try:
            self._click(loc)
            self._wait(900)
        except Exception as exc:
            self._log(f"Không bấm được More Options: {exc}")
            return
        if not self.options_open():
            self._log("Đã bấm More Options nhưng các thanh trượt chưa hiện.")

    # ── form ─────────────────────────────────────────────────────
    def clear_form(self) -> None:
        """Bấm «Clear all form inputs» (bỏ file/lời của bài trước). Có hộp hỏi lại
        thì bấm Clear trong hộp đó."""
        loc = self._find("clear_form", timeout_ms=4000, required=False)
        if loc is None:
            self._log("Không thấy nút xoá form — bỏ qua.")
            return
        try:
            self._click(loc)
        except Exception:
            return
        self._wait(700)
        dlg = self._top_dialog()
        if dlg:
            for b in dlg["buttons"]:
                if b["enabled"] and b["text"].strip().lower() in (
                        "confirm", "clear", "clear all", "yes"):
                    self._click(self.page.locator(f"[data-tool-dlg='{b['i']}']").first)
                    self._wait(500)
                    break
        self._log("Đã xoá form Create.")

    def fill_title(self, title: str) -> bool:
        loc = None
        # Panel Create virtualize phần dưới: Title chưa có trong DOM cho tới khi
        # cuộn chính panel trái xuống đáy (scroll_into_view chưa đủ).
        try:
            scrolled = self.page.evaluate(r"""() => {
              const anchors=[...document.querySelectorAll(
                "[data-testid='create-form-styles-wrapper'],[aria-label='Lyrics editor'],textarea")];
              for (const anchor of anchors) {
                let node=anchor;
                for (let i=0;i<10 && node;i++,node=node.parentElement) {
                  if (node.scrollHeight > node.clientHeight + 80 && node.clientHeight > 300) {
                    const r=node.getBoundingClientRect();
                    if (r.x < innerWidth*.35 && r.width < innerWidth*.4) {
                      node.scrollTop=node.scrollHeight;
                      return {top:node.scrollTop,height:node.scrollHeight,client:node.clientHeight};
                    }
                  }
                }
              }
              const panes=[...document.querySelectorAll('div')].filter(node => {
                const r=node.getBoundingClientRect();
                return r.width>250 && r.width<650 && r.height>350 &&
                  r.x>150 && r.x<innerWidth*.35 &&
                  node.scrollHeight>node.clientHeight+80;
              }).sort((a,b)=>(b.scrollHeight-b.clientHeight)-(a.scrollHeight-a.clientHeight));
              if (panes.length) {
                const node=panes[0];
                node.scrollTop=node.scrollHeight;
                return {top:node.scrollTop,height:node.scrollHeight,client:node.clientHeight,
                        fallback:true};
              }
              return null;
            }""")
            if scrolled:
                self._log(f"Đã cuộn panel Create xuống đáy: {scrolled}.")
                self._wait(800)
        except Exception:
            pass
        # Tìm trong DOM rồi scroll chính control vào vùng nhìn thấy.
        for selector in self.sel.get("song_title", []):
            try:
                candidate = self.page.locator(selector).first
                if candidate.count() > 0:
                    candidate.scroll_into_view_if_needed(timeout=4000)
                    self._wait(300)
                    if candidate.is_visible():
                        loc = candidate
                        break
            except Exception:
                continue
        if loc is None:
            try:
                marked = self.page.evaluate(_JS_MARK_SONG_TITLE)
            except Exception:
                marked = False
            if marked:
                candidate = self.page.locator("[data-tool-song-title='1']").first
                try:
                    candidate.scroll_into_view_if_needed(timeout=4000)
                    self._wait(300)
                    if candidate.is_visible():
                        loc = candidate
                except Exception:
                    loc = None
        if loc is None:
            self._log("Suno không hiển thị Song Title ở chế độ Cover — giữ tên "
                      "kế thừa từ bài Editor (trường này là tùy chọn).")
            return False
        try:
            loc.click()
            editable = (loc.get_attribute("contenteditable") or "").lower() == "true"
            if editable:
                self.page.keyboard.press("Control+A")
                self.page.keyboard.insert_text(title)
            else:
                loc.fill(title)
            self._wait(200)
            got = (loc.inner_text() if editable else loc.input_value())
        except Exception as e:
            raise SunoUIChanged(f"Không điền được tên bài: {e}") from e
        if got.strip() != title.strip():
            raise SunoUIChanged(f"Ô tên bài hiện «{got}» thay vì «{title}».")
        self._log(f"Tên bài: {title}")
        return True

    def fill_song_lyrics(self, text: str) -> None:
        """Điền LỜI BÀI HÁT (khác fill_lyrics của tool cũ chỉ cho thẻ cấu trúc)."""
        from .lyrics import strip_lyrics_header
        text = strip_lyrics_header((text or "").replace("\r", "")).strip()
        if not text:
            raise SunoError("Bài chưa có lời — dán lời ở tab ① trước.")
        if len(text) > LYRICS_LIMIT:
            raise SunoError(f"Lời dài {len(text)} ký tự, Suno chỉ nhận tối đa "
                            f"{LYRICS_LIMIT} — rút bớt lời ở tab ① rồi thử lại.")
        loc = self._find("lyrics_editor", timeout_ms=6000)
        if (loc.get_attribute("data-cowrite-input") or "").lower() == "true":
            raise SunoUIChanged("Selector Lyrics trỏ nhầm ô Cowriter (nhờ Suno viết lời).")
        lines = [ln.strip() for ln in text.splitlines()]
        want = [ln for ln in lines if ln]

        def read_back() -> list[str]:
            got = (loc.inner_text() or "").strip()
            return [ln.strip() for ln in got.replace("\r", "").splitlines() if ln.strip()]

        def clear() -> None:
            loc.click()
            self.page.keyboard.press("Control+A")
            self.page.keyboard.press("Delete")

        try:
            # Nhanh: dán cả lời một lần (sự kiện paste của ô Lexical) thay vì gõ
            # từng dòng (~13 giây cho 38 câu). Đọc lại không khớp → gõ từng dòng.
            clear()
            try:
                loc.evaluate(_JS_PASTE_TEXT, "\n".join(lines))
                self._wait(300)
                actual = read_back()
            except Exception:
                actual = []
            if actual != want:
                clear()
                for i, ln in enumerate(lines):
                    if ln:
                        self.page.keyboard.insert_text(ln)
                    if i < len(lines) - 1:
                        self.page.keyboard.press("Enter")
                self._wait(400)
                actual = read_back()
        except Exception as e:
            raise SunoUIChanged(f"Không điền được lời: {e}") from e
        if actual != want:
            raise SunoUIChanged(
                f"Lyrics trên form không khớp toàn bộ lời chuẩn ({len(actual)}/{len(want)} dòng).")
        self._log(f"Đã điền lời ({len(want)} câu).")

    def set_vocal_gender(self, gender: str) -> None:
        """gender = male | female. Song ca được khóa trong Styles, không bấm UI."""
        want = "Male" if gender == "male" else "Female"
        # Suno vẫn giữ hai nút Male/Female trong DOM khi ``More Options`` đang
        # thu gọn.  Vì vậy không được dùng sự tồn tại của hàng này để suy ra nó
        # đã mở: mở panel trước, rồi mới dò và bấm lựa chọn.
        self.ensure_more_options()
        st = self.page.evaluate(_JS_PAIR_ROW, ["Vocal Gender", "Male", "Female", want])
        if st is None:
            self._log("Không thấy mục Vocal Gender ở chế độ Cover — Styles vẫn khóa "
                      f"{want.lower()} vocal, tiếp tục.")
            return
        other = "Female" if want == "Male" else "Male"
        if st.get(want) and not st.get(other):
            self._log(f"Vocal Gender = {want}.")
            return
        if st.get("desiredDisabled"):
            self._log(f"Nút Vocal Gender {want} đang disabled — Styles vẫn khóa "
                      f"{want.lower()} vocal, tiếp tục.")
            return
        # Chỉ bấm MỘT lần. UI mới giấu trạng thái; bấm lặp có thể bật rồi tắt.
        self._click(self.page.locator("[data-tool-pair='1']").first)
        self._wait(700)
        after = self.page.evaluate(_JS_PAIR_ROW,
                                   ["Vocal Gender", "Male", "Female", want])
        if after and after.get(want) and not after.get(other):
            self._log(f"Vocal Gender = {want}.")
            return
        if not after or not after.get("verifiable"):
            self._log(f"Đã bấm Vocal Gender = {want}; Suno không công khai trạng thái "
                      f"chọn qua DOM. Styles đã khóa {want.lower()} vocal — tiếp tục.")
            return
        # Trạng thái có thể kiểm chứng mà vẫn sai: lưu ảnh chẩn đoán nhưng không
        # dừng cả hàng, vì giới tính đã được khóa thêm trong Styles.
        shot = self.save_debug("vocal_gender")
        self._log(f"Suno không xác nhận nút Vocal Gender = {want} ({after}); "
                  f"tiếp tục bằng khóa {want.lower()} vocal trong Styles"
                  + (f". Ảnh: {shot}" if shot else "."))

    def set_creative_sliders(self, values: dict) -> dict:
        out = {}
        for key in ("weirdness", "style_influence", "variety", "audio_influence"):
            if values.get(key) is None:
                continue
            if key == "variety" and self._slider(key) is None:
                self._log("Không thấy thanh Variety ở chế độ Cover — bỏ qua.")
                continue
            out[key] = self.set_slider(key, int(values[key]))
        if out:
            self._log("Thanh trượt: " + ", ".join(
                f"{self.SLIDERS[k]}={v}" for k, v in out.items()))
        return out

    # ── đính bài gốc + chọn Cover ────────────────────────────────
    def cover_ready(self) -> bool:
        """Form đang ở chế độ Cover có file gốc = thấy thanh «Audio Influence»."""
        try:
            return self.page.locator(
                "[role=slider][aria-label='Audio Influence']").count() > 0
        except Exception:
            return False

    def _click_for_chooser(self, loc, path: Path) -> bool:
        try:
            with self.page.expect_file_chooser(timeout=5000) as fc:
                self._click(loc)
            fc.value.set_files(str(path))
            return True
        except Exception:
            return False

    def _pick_file(self, path: Path) -> str:
        btn = query_first(self.page, self.sel.get("cover_audio_button") or _AUDIO_BUTTONS,
                          timeout_ms=8000)
        if btn is None:
            raise self._ui_changed("Không thấy nút «Audio» (Add audio) trong form Create.",
                                   "no_audio_button")
        if self._click_for_chooser(btn, path):
            return "nút Audio"
        up = query_first(self.page, self.sel.get("cover_upload_item") or _UPLOAD_ITEMS,
                         timeout_ms=4000)
        if up is not None and self._click_for_chooser(up, path):
            return "Audio → Upload"
        inp = self.page.locator("input[type=file]")
        for i in range(inp.count()):
            acc = (inp.nth(i).get_attribute("accept") or "").lower()
            if not acc or "audio" in acc or ".mp3" in acc or ".wav" in acc:
                inp.nth(i).set_input_files(str(path))
                return "ô chọn file ẩn"
        raise self._ui_changed("Không mở được hộp chọn file sau khi bấm «Audio».",
                               "no_file_chooser")

    def _choose_cover(self) -> bool:
        """Bấm lựa chọn «Cover» nếu đang hiện và chưa chọn. True nếu đã bấm."""
        try:
            st = self.page.evaluate(_JS_MARK_COVER)
        except Exception:
            st = None
        if not st or st.get("selected"):
            return False
        self._click(self.page.locator("[data-tool-cover='1']").first)
        self._log("Đã chọn «Cover».")
        self._wait(1200)
        return True

    def _handle_dialog(self, human: dict, choose_cover: bool = True) -> bool:
        """Xử lý hộp thoại đang hiện sau khi tải file lên. True nếu còn hộp thoại
        (đang chờ người dùng hoặc vừa bấm «làm tiếp»)."""
        dlg = self._top_dialog()
        if not dlg:
            human["since"] = None
            return False
        now = time.time()
        if TERMS_RE.search(dlg["text"]):
            if human["since"] is None:
                human["since"] = now
                human["nag"] = now
                self.save_debug("terms")
                self._alert_human(
                    "⚠️ CẦN BẠN: Suno hỏi xác nhận điều khoản / quyền dùng bản ghi âm. "
                    "Hãy TỰ đọc và bấm trong cửa sổ Suno (tool không tự đồng ý thay "
                    "bạn) — tool chờ rồi làm tiếp.", self.on_human, beep=True)
            elif now - human["nag"] >= 120:
                human["nag"] = now
                self._alert_human("⚠️ Vẫn đang chờ bạn bấm xác nhận trong cửa sổ Suno.",
                                  self.on_human, beep=True)
            return True
        if choose_cover and self._choose_cover():
            return True
        for name in _NEXT_BUTTONS:
            b = next((b for b in dlg["buttons"]
                      if b["enabled"] and b["text"].strip().lower() == name.lower()), None)
            if b is not None:
                self._click(self.page.locator(f"[data-tool-dlg='{b['i']}']").first)
                self._log(f"Hộp thoại tải lên: bấm «{b['text']}».")
                self._wait(1200)
                return True
        return True        # hộp thoại đang xử lý (vd «Uploading…») → chờ tiếp

    def _wait_upload_dialog_clear(self, human_wait_sec: float = 1800) -> None:
        """Không cho workflow Editor thao tác xuyên qua modal upload.

        Khi resume, hàng UPLOAD có thể đã tồn tại nên `_open_uploaded_audio_in_editor`
        đi thẳng vào Editor và bỏ qua vòng `_handle_dialog` của lần upload mới. Nếu
        Suno vẫn giữ hộp ``Upload audio / Agree to Terms`` ở trên cùng, locator Tempo
        phía sau modal vẫn tồn tại nhưng mọi click đều bị overlay chặn. Giữ job chờ
        người dùng xác nhận quyền sử dụng bản ghi, giống hệt nhánh upload mới.
        """
        human = {"since": None, "nag": 0.0}
        deadline = time.time() + max(1.0, float(human_wait_sec))
        while time.time() < deadline:
            dlg = self._top_dialog()
            if not dlg:
                return
            if TERMS_RE.search(dlg.get("text") or ""):
                self._handle_dialog(human, choose_cover=False)
                self._wait(1000)
                continue
            # Các modal tiến trình/Continue thông thường vẫn được xử lý tự động.
            self._handle_dialog(human, choose_cover=False)
            self._wait(1000)
        if human["since"] is not None:
            raise SunoError(
                "Hết giờ chờ bạn bấm «Agree to Terms» trong hộp Upload audio của "
                "Suno — bấm «Làm lại trên Suno» sau khi đã xác nhận.")
        raise self._ui_changed(
            "Suno còn một hộp thoại che Editor nên chưa thể chỉnh Tempo.",
            "editor_blocked_by_dialog")

    # ── upload → Editor → Save as New Song ──────────────────────
    def _editor_control(self, key: str, fallback: list[str], timeout_ms: int = 5000):
        return query_first(self.page, self.sel.get(key) or fallback, timeout_ms=timeout_ms)

    def _open_editor_from_song(self, song_id: str) -> None:
        """Mở ⋯ → Edit → Open in Editor của đúng audio UPLOAD vừa tạo."""
        self._log(f"Audio upload đã xuất hiện ({song_id}) — mở menu ⋯ → Edit.")
        self._ensure_song_open(song_id)
        problem = ""
        for attempt in range(1, 4):
            self._open_song_menu(song_id)
            problem = self._editor_from_open_menu()
            if not problem:
                self._log("Đã bấm Edit → Open in Editor.")
                return
            self._log(f"{problem} (lần {attempt}/3); menu đang thấy: "
                      f"{self._menu_items_text()} — mở lại.")
        raise self._ui_changed(
            f"Audio upload: {problem} sau 3 lần mở menu ⋯.", "no_open_in_editor_menu")

    def _menu_item(self, label: str, timeout_ms: int = 3000):
        """Mục menu đang hiện có DÒNG ĐẦU đúng bằng ``label`` (vd «Edit», «Open in
        Editor» — bỏ huy hiệu «Pro»/«New» ở dòng sau). Chữ của Suno nằm trong
        <span> con nên ``:text-is`` của Playwright không khớp mục menu (LIVE
        2026-11-08) → đánh dấu bằng JS rồi lấy locator."""
        deadline = time.time() + timeout_ms / 1000.0
        key = label.lower()
        while True:
            try:
                if self.page.evaluate(_JS_MARK_MENU_ITEM, label):
                    loc = self.page.locator(f"[data-tool-menu-item='{key}']").first
                    if loc.count() > 0:
                        return loc
            except Exception:
                pass
            if time.time() >= deadline:
                return None
            self._wait(250)

    def _click_menu_item(self, loc) -> None:
        """Bấm mục trong submenu: rê chuột thật theo nhiều bước tới giữa mục rồi
        click (không cuộn trang — cuộn làm menu đóng). Mục nằm ngoài khung nhìn
        → gửi sự kiện click thẳng vào mục."""
        try:
            box = loc.bounding_box()
            w, h = self.page.evaluate("() => [innerWidth, innerHeight]")
        except Exception:
            box, w, h = None, 0, 0
        if box:
            cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            if 0 < cx < w and 0 < cy < h - 2:
                self.page.mouse.move(cx, cy, steps=6)
                self._wait(150)
                self.page.mouse.click(cx, cy)
                return
        loc.dispatch_event("click")

    def _editor_from_open_menu(self) -> str:
        """Menu ⋯ của bài đang mở → Edit → Open in Editor. Trả "" nếu đã bấm,
        còn không thì trả mô tả chỗ hụt (để người gọi mở lại menu)."""
        edit = self._menu_item("Edit", timeout_ms=3000)
        if edit is None:
            return "Menu ⋯ chưa có mục «Edit»"
        open_editor = None
        for how in ("hover", "click", "key"):
            try:
                if how == "hover":
                    if not self._hover_real(edit):
                        edit.hover()
                elif how == "click":
                    self._click(edit)
                else:
                    edit.focus()
                    self.page.keyboard.press("ArrowRight")
            except Exception:
                continue
            self._wait(600)
            open_editor = self._menu_item("Open in Editor", timeout_ms=1800)
            if open_editor is not None:
                break
        if open_editor is None:
            return "Submenu Edit chưa bung «Open in Editor»"
        try:
            start_url = str(self.page.url or "")
        except Exception:
            start_url = ""
        try:
            self._click_menu_item(open_editor)
        except Exception as e:
            return f"Không bấm được «Open in Editor» ({type(e).__name__})"
        # LIVE 2026-11-09 02:43: menu đóng ngay trước cú bấm → chuột trúng hàng bài
        # khác (mở panel «Rượu Mừng…»), URL vẫn /create, tool tưởng đã vào Editor
        # rồi dừng vì không thấy ô Tempo. Nay phải thấy URL /edit/<id> mới coi là xong.
        if self._wait_editor_url(8000):
            self._wait(1000)
            return ""
        try:
            now_url = str(self.page.url or "")
        except Exception:
            now_url = ""
        self._dismiss_popups()
        if start_url and now_url != start_url:
            try:
                self.page.goto(start_url)
                self._wait(2500)
            except Exception:
                pass
        return f"Đã bấm «Open in Editor» nhưng Editor chưa mở (trang: {now_url or '?'})"

    def _wait_editor_url(self, timeout_ms: int) -> bool:
        """True khi trang đã sang Suno Editor (URL https://suno.com/edit/<id>)."""
        deadline = time.time() + timeout_ms / 1000.0
        while True:
            try:
                if "/edit/" in str(self.page.url or ""):
                    return True
            except Exception:
                pass
            if time.time() >= deadline:
                return False
            self._wait(250)

    def _menu_items_text(self) -> str:
        """Chữ của các mục trong menu đang mở — để ghi log khi menu sai."""
        try:
            items = self.page.evaluate(_JS_OPEN_MENU_ITEMS)
        except Exception:
            items = []
        return ", ".join(items) if items else "(không có menu nào đang mở)"

    def _mark_upload_row(self, path: Path) -> bool:
        try:
            return bool(self.page.evaluate(_JS_MARK_UPLOAD_MORE, Path(path).stem))
        except Exception:
            return False

    def _open_matching_upload_in_editor(self, path: Path, rounds: int = 3) -> bool:
        """Mở Editor từ hàng ``UPLOAD`` (không có UUID) theo tên file.

        Submenu Radix của Suno thỉnh thoảng không bung (danh sách render lại,
        menu đóng khi rê chuột…). Mỗi vòng: đóng popup → đánh dấu lại đúng hàng →
        bấm ⋯ → chọn mục đúng chữ «Edit» → mở submenu → «Open in Editor». Hụt
        thì làm lại tối đa ``rounds`` vòng. Vẫn không được → False (người gọi tự
        upload lại file), KHÔNG dừng cả hàng đợi.
        """
        if not self._mark_upload_row(path):
            return False
        for attempt in range(1, rounds + 1):
            self._dismiss_popups()
            # Escape có thể làm virtual list render lại → đánh dấu lại.
            if not self._mark_upload_row(path):
                return False
            more = self.page.locator("button[data-tool-upload-more='1']").first
            try:
                if not self._real_click(more):
                    more.click(timeout=4000)
            except Exception:
                continue
            self._wait(600)
            problem = self._editor_from_open_menu()
            if problem:
                self._log(f"Hàng UPLOAD: {problem} (lần {attempt}/{rounds}); menu đang "
                          f"thấy: {self._menu_items_text()} — mở lại.")
                continue
            self._log("Đã mở hàng UPLOAD theo tên file → Edit → Open in Editor.")
            return True
        self._dismiss_popups()
        self._log(f"Không mở được Editor từ hàng UPLOAD cũ sau {rounds} lần — "
                  "sẽ upload lại file.")
        return False

    def _open_uploaded_audio_in_editor(self, path: Path, before_ids: set[str],
                                       human_wait_sec: float = 1800) -> None:
        """Upload audio, nhận diện hàng UPLOAD mới rồi vào menu Editor của nó."""
        # Resume sau lỗi: hàng UPLOAD có thể đã tồn tại từ lần trước. Dùng lại để
        # không upload một bản giống hệt và không tạo thêm asset rác.
        if self._open_matching_upload_in_editor(path):
            self._log("Dùng lại audio UPLOAD đã có từ lần chạy trước.")
            return
        how = self._pick_file(path)
        self._log(f"Đã chọn file «{path.name}» ({how}) — chờ nút Open in Editor…")
        human = {"since": None, "nag": 0.0}
        deadline = time.time() + 180
        while time.time() < deadline:
            self.check_rejection()
            button = self._editor_control("open_in_editor", _OPEN_EDITOR, timeout_ms=1200)
            if button is not None:
                self._click(button)
                self._wait(1800)
                self._log("Đã bấm Open in Editor.")
                return
            fresh = self.snapshot_song_ids() - before_ids
            if len(fresh) == 1:
                self._open_editor_from_song(next(iter(fresh)))
                return
            busy = self._handle_dialog(human, choose_cover=False)
            if not busy and self._open_matching_upload_in_editor(path, rounds=1):
                return
            if human["since"] is not None:
                deadline = max(deadline, human["since"] + human_wait_sec)
            self._wait(1200)
        if human["since"] is not None:
            raise SunoError("Hết giờ chờ bạn xác nhận quyền dùng bản ghi âm trên Suno.")
        raise self._ui_changed(
            "Upload xong nhưng không thấy nút «Open in Editor» — tool chưa điền "
            "Title và chưa bấm Create.", "no_open_in_editor")

    def _find_editor_tempo(self):
        loc = self._editor_control("editor_tempo", _EDITOR_TEMPO, timeout_ms=10000)
        if loc is not None:
            try:
                disabled = (not loc.is_enabled()) or \
                    (loc.get_attribute("disabled") is not None) or \
                    (loc.get_attribute("aria-disabled") == "true")
            except Exception:
                disabled = False
            if not disabled:
                return loc
            self._log("Bỏ qua input number disabled của Suno; tiếp tục tìm "
                      "control Tempo đang hiển thị.")
        try:
            marked = self.page.evaluate(_JS_MARK_EDITOR_TEMPO)
        except Exception:
            marked = False
        if marked:
            return query_first(self.page, ["[data-tool-editor-tempo='1']"], timeout_ms=2000)
        try:
            numeric_input = self.page.evaluate(_JS_MARK_EDITOR_TEMPO_INPUT)
        except Exception:
            numeric_input = None
        if numeric_input:
            return query_first(self.page, ["[data-tool-editor-tempo-input='1']"],
                               timeout_ms=2000)
        return None

    def _wait_editor_loaded(self, timeout_sec: float = 95.0) -> None:
        """Editor Beta đôi lúc đứng ở “Loading Edit Session…”. Chờ tải thật;
        sau 45 giây dùng nút Legacy Editor chính Suno cung cấp làm fallback."""
        started = time.time()
        legacy_clicked = False
        while time.time() - started < timeout_sec:
            try:
                body = self.page.locator("body").inner_text(timeout=2000)
            except Exception:
                return  # test double / trang đã sẵn sàng nhưng không đọc được body
            loading = bool(re.search(r"loading\s+edit\s+session", body or "", re.I))
            if not loading:
                if legacy_clicked:
                    self._wait(1200)
                return
            elapsed = time.time() - started
            if elapsed >= 45 and not legacy_clicked:
                legacy = query_first(self.page, [
                    "button:text-is('Open in Legacy Editor')",
                    "[role=button]:text-is('Open in Legacy Editor')",
                    "button:has-text('Legacy Editor')",
                ], timeout_ms=2500)
                if legacy is not None:
                    self._log("Editor Beta tải quá lâu — chuyển sang Legacy Editor của Suno.")
                    self._click(legacy)
                    legacy_clicked = True
            self._wait(1000)
        probe = self._save_editor_probe()
        raise self._ui_changed(
            "Suno Editor vẫn ở trạng thái «Loading Edit Session» sau khi đã chờ "
            "và thử Legacy Editor."
            + (f" DOM: {probe}." if probe else ""), "editor_loading_timeout")

    def set_editor_tempo(self, bpm: int) -> int:
        """Đặt BPM trong Suno Editor và trả lại chính giá trị đọc từ UI.

        Giá trị trả về không phải BPM trong prompt. Nó được đọc lại từ input /
        stepper của Editor sau thao tác để caller có thể lưu và hiển thị đúng
        trạng thái thực tế thay vì tiếp tục ghi ``Auto``.
        """
        if not 20 <= int(bpm) <= 300:
            raise SunoError(f"Tempo Editor phải nằm trong khoảng 20–300 BPM: {bpm}.")
        self._wait_editor_loaded()
        # Một số session mở ở chế độ BPM khóa. Bấm bánh răng cạnh con số trước
        # rồi mới dò input/stepper thật; không thao tác spinner của input disabled.
        try:
            locked = self.page.evaluate(_JS_MARK_EDITOR_TEMPO_GEAR)
        except Exception:
            locked = None
        if locked:
            self._log(f"Tempo Editor đang khóa ở {int(locked['value'])}; "
                      "bấm nút bánh răng để bật chỉnh BPM.")
            try:
                gear = self.page.locator("[data-tool-tempo-gear='1']").first
                gear.click(timeout=3000)
                self._wait(500)
                manual = query_first(self.page, [
                    "button:has-text('Manual BPM')",
                    "[role=button]:has-text('Manual BPM')",
                    "[role=menuitem]:has-text('Manual BPM')",
                    "text=Manual BPM (Consistent)",
                ], timeout_ms=3500)
                if manual is not None:
                    self._click(manual)
                    self._log("Đã chọn Manual BPM (Consistent).")
                self._wait(700)
            except Exception as exc:
                raise SunoUIChanged(
                    f"Không mở được chế độ chỉnh Tempo từ nút bánh răng: {exc}") from exc
        # Ưu tiên control thật đang hiện ở thanh Editor: ``− 91 +``. Suno còn
        # render một input number bị disabled trong Base UI portal (ví dụ 169);
        # chọn input đó trước sẽ vừa đọc sai Tempo vừa gây timeout khi click.
        try:
            stepper = self.page.evaluate(_JS_MARK_EDITOR_TEMPO_STEPPER)
        except Exception:
            stepper = None
        if stepper:
            current = int(stepper["value"])
            direction = "up" if bpm > current else "down"
            step = self.page.locator(
                f"[data-tool-tempo-step='{direction}']").first
            count = abs(bpm - current)
            self._log(f"Đã đọc Tempo Editor hiện tại = {current}; "
                      f"bấm {direction} {count} lần.")
            try:
                for _ in range(count):
                    step.click(timeout=2000)
                    self._wait(35)
                self._wait(500)
                after = self.page.evaluate(_JS_MARK_EDITOR_TEMPO_STEPPER)
            except Exception as exc:
                raise SunoUIChanged(
                    f"Không bấm được nút chỉnh Tempo trong Editor: {exc}") from exc
            actual = int(after["value"]) if after else -1
            if actual != bpm:
                raise SunoUIChanged(
                    f"Đã chỉnh stepper Tempo nhưng kết quả là {actual}, cần {bpm}.")
            self._log(f"Tempo trong Suno Editor = {actual}.")
            return actual
        loc = self._find_editor_tempo()
        if loc is None:
            probe = self._save_editor_probe()
            raise self._ui_changed("Không tìm thấy ô hoặc cụm nút Tempo/BPM trong "
                                   "Suno Editor."
                                   + (f" DOM: {probe}." if probe else ""),
                                   "no_editor_tempo")
        try:
            kind = (loc.get_attribute("type") or "").lower()
            role = (loc.get_attribute("role") or "").lower()
            editable = (loc.get_attribute("contenteditable") or "").lower() == "true"
            if kind == "range" or role == "slider":
                loc.evaluate("(el, value) => { el.value = String(value); "
                             "el.dispatchEvent(new Event('input', {bubbles:true})); "
                             "el.dispatchEvent(new Event('change', {bubbles:true})); }", bpm)
            elif editable:
                loc.click()
                self.page.keyboard.press("Control+A")
                self.page.keyboard.insert_text(str(bpm))
                self.page.keyboard.press("Enter")
            else:
                try:
                    loc.click(timeout=5000)
                    loc.fill(str(bpm))
                    self.page.keyboard.press("Enter")
                    self.page.keyboard.press("Tab")
                except Exception as click_exc:
                    # Editor mới của Suno đặt input BPM trong một Base UI portal.
                    # Có lúc modal-overlay vẫn còn nhận pointer dù input đã visible,
                    # enabled và stable. Điền thẳng đúng input (không click xuyên
                    # sang control khác), phát đủ input/change/blur rồi bắt buộc
                    # đọc lại giá trị ở dưới để xác nhận.
                    if "intercepts pointer events" not in str(click_exc).lower() and \
                            "modal-overlay" not in str(click_exc).lower():
                        raise
                    self._log("Overlay Suno che ô Tempo — dùng fallback điền trực "
                              "tiếp vào input BPM và kiểm tra lại giá trị.")
                    loc.fill(str(bpm), force=True)
                    loc.evaluate("""el => {
                      el.dispatchEvent(new Event('input', {bubbles:true}));
                      el.dispatchEvent(new Event('change', {bubbles:true}));
                      el.blur();
                    }""")
            self._wait(700)
            value = (loc.get_attribute("aria-valuenow") or
                     (loc.inner_text() if editable else loc.input_value()) or "").strip()
        except Exception as exc:
            raise SunoUIChanged(f"Không chỉnh được Tempo trong Editor: {exc}") from exc
        try:
            actual = int(round(float(value.replace(",", "."))))
        except (TypeError, ValueError):
            actual = -1
        if actual != bpm:
            raise SunoUIChanged(f"Tempo trong Editor đang là «{value}», cần {bpm}.")
        self._log(f"Tempo trong Suno Editor = {actual}.")
        return actual

    @staticmethod
    def _song_id_in_url(url: str) -> str:
        found = re.search(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            url or "", re.I)
        return found.group(0) if found else ""

    def _pick_saved_song(self, fresh: set[str], hint: str) -> str:
        """Chọn bài Editor vừa lưu trong ``fresh`` (ID chưa có trước khi Save).

        LIVE 2026-11-09: ảnh chụp danh sách ``before`` có lúc thiếu vài bài cũ
        (danh sách ảo/đang nạp) → ``fresh`` có >1 ID dù Suno chỉ lưu 1 bài, code
        cũ đòi đúng 1 nên báo «không xác định được bài mới». Nay: nhiều ID thì
        lấy bài ĐẦU danh sách (sắp xếp New) có tên «<tên file> … (Edit)»."""
        if len(fresh) == 1:
            return next(iter(fresh))
        want = _fold_title(hint)
        if not fresh or not want:
            return ""
        try:
            links = self.page.evaluate(_JS_SONG_LINKS) or []
        except Exception:
            links = []
        for href, text in links:
            found = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
                              r"[0-9a-f]{12}", href or "", re.I)
            if not found or found.group(0) not in fresh:
                continue
            folded = _fold_title(text)
            if want in folded and "(edit)" in folded:
                return found.group(0)
        return ""

    def save_editor_as_new_song(self, before_ids: set[str], title_hint: str = "") -> str:
        button = self._editor_control("save_as_new_song", _SAVE_AS_NEW, timeout_ms=12000)
        if button is None:
            raise self._ui_changed("Không thấy nút «Save as New Song» trong Editor.",
                                   "no_save_as_new_song")
        old_url_id = self._song_id_in_url(self.page.url or "")
        before_ids = set(before_ids)
        if old_url_id:
            before_ids.add(old_url_id)
        self._click(button)
        self._wait(900)
        dialog = self._top_dialog()
        if dialog:
            confirm = next((item for item in dialog["buttons"] if item["enabled"] and
                            item["text"].strip().lower() in
                            ("save as new song", "save", "confirm")), None)
            if confirm is not None:
                self._click(self.page.locator(
                    f"[data-tool-dlg='{confirm['i']}']").first)
                self._wait(700)
        self._log("Đã bấm Save as New Song — chờ bài mới xuất hiện…")
        deadline = time.time() + 120
        while time.time() < deadline:
            self.check_rejection()
            fresh = self.snapshot_song_ids() - before_ids
            current = self._song_id_in_url(self.page.url or "")
            if current and current != old_url_id and current not in before_ids:
                return current
            if current and current in fresh:
                return current
            picked = self._pick_saved_song(fresh, title_hint)
            if picked:
                return picked
            self._wait(1500)
        # Editor đôi khi không render danh sách bài. Về Create để đọc thư viện
        # (danh sách nạp dần → đọc lại vài lần).
        self.open_create_page()
        self._clear_library_filters()
        for _ in range(6):
            picked = self._pick_saved_song(self.snapshot_song_ids() - before_ids,
                                           title_hint)
            if picked:
                return picked
            self._wait(1500)
        raise self._ui_changed(
            "Đã bấm «Save as New Song» nhưng không xác định được bài mới để mở "
            "Remix → Cover; tool chưa bấm Create.", "saved_song_not_found")

    def prepare_editor_source(self, path: Path, bpm: int,
                              human_wait_sec: float = 1800) -> str:
        path = Path(path)
        if not path.exists():
            raise SunoError(f"Không thấy file đã chỉnh tempo: {path}")
        self._clear_library_filters()
        before = self.snapshot_song_ids()
        self._open_uploaded_audio_in_editor(path, before, human_wait_sec)
        self._wait_upload_dialog_clear(human_wait_sec)
        actual_bpm = self.set_editor_tempo(bpm)
        # Giữ kết quả đã đọc lại từ UI cho lớp job lưu vào SourceSong. Dùng
        # thuộc tính thay vì đổi contract prepare_cover để các driver/plugin cũ
        # vẫn tương thích.
        self.last_editor_tempo = actual_bpm
        # Upload có thể tự thêm một audio item vào thư viện trước khi Save. Loại
        # item đó khỏi phép so để nhận đúng bài mới do Editor tạo ra.
        before_save = before | self.snapshot_song_ids()
        song_id = self.save_editor_as_new_song(before_save, title_hint=path.stem)
        self._log(f"Đã lưu bài Editor mới ({song_id}) với Tempo {bpm}.")
        return song_id

    def read_editor_tempo(self) -> Optional[int]:
        """Đọc (không chỉnh) Tempo đang hiện ở cụm ``− N +`` của Suno Editor."""
        try:
            info = self.page.evaluate(_JS_MARK_EDITOR_TEMPO_STEPPER)
            return int(info["value"]) if info else None
        except Exception:
            return None

    def wait_user_editor_save(self, path: Path, bpm: int,
                              alert: Optional[Callable[[str], None]] = None,
                              timeout_sec: float = 1800) -> str:
        """Chế độ BÁN THỦ CÔNG: tool upload file tempo + mở Suno Editor; NGƯỜI DÙNG
        tự chỉnh Tempo và tự bấm «Save as New Song». Tool chỉ theo dõi để nhận
        đúng bài vừa lưu (ID mới trên URL Editor, hoặc bài «<tên file> … (Edit)»
        mới trong thư viện) rồi trả ID đó. Tool không bấm nút nào trong Editor."""
        path = Path(path)
        if not path.exists():
            raise SunoError(f"Không thấy file đã chỉnh tempo: {path}")
        self._clear_library_filters()
        before = self.snapshot_song_ids()
        self._open_uploaded_audio_in_editor(path, before, timeout_sec)
        self._wait_upload_dialog_clear(timeout_sec)
        self._wait_editor_loaded()
        before_save = before | self.snapshot_song_ids()
        editor_id = self._song_id_in_url(self.page.url or "")
        if editor_id:
            before_save.add(editor_id)
        msg = (f"✋ CẦN BẠN: trong cửa sổ Suno Editor hãy chỉnh Tempo (gợi ý {bpm} BPM) "
               "rồi bấm «Save as New Song». Tool tự nhận bài vừa lưu và làm tiếp "
               "Remix → Cover + điền lyrics/style.")
        self._log("Đã mở Suno Editor — chờ bạn tự chỉnh Tempo và bấm «Save as New Song» "
                  f"(tối đa {int(timeout_sec // 60)} phút).")
        (alert or self.on_human or self._log)(msg)
        seen_bpm: Optional[int] = None
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            self.check_rejection()
            now_bpm = self.read_editor_tempo()
            if now_bpm is not None:
                seen_bpm = now_bpm
            current = self._song_id_in_url(self.page.url or "")
            song_id = current if current and current not in before_save else ""
            if not song_id:
                song_id = self._pick_saved_song(self.snapshot_song_ids() - before_save,
                                                path.stem)
            if song_id:
                self.last_editor_tempo = seen_bpm if seen_bpm is not None else int(bpm)
                self._log(f"Nhận bài Editor bạn vừa lưu ({song_id}); Tempo đọc được "
                          f"trong Editor: {seen_bpm if seen_bpm is not None else 'không đọc được'}"
                          f"{'' if seen_bpm in (None, int(bpm)) else f' (gợi ý là {bpm})'}.")
                return song_id
            self._wait(1500)
        raise SunoError(f"Hết {int(timeout_sec // 60)} phút chờ bạn bấm «Save as New Song» "
                        "trong Suno Editor — tool chưa điền form, chưa Create.")

    def _settle(self, timeout_sec: float, human: dict, human_wait_sec: float) -> bool:
        """Chờ form vào chế độ Cover: bấm Cover/làm tiếp khi cần, dừng nếu Suno từ
        chối. True = đã thấy thanh Audio Influence."""
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            self.check_rejection()
            busy = self._handle_dialog(human)
            if human["since"] is not None:
                deadline = max(deadline, human["since"] + human_wait_sec)
            if not busy:
                self._choose_cover()
                self.ensure_more_options()
                if self.cover_ready():
                    return True
            self._wait(1500)
        if human["since"] is not None:
            raise SunoError("Hết giờ chờ bạn xác nhận trong cửa sổ Suno — bấm «Thử "
                            "lại» khi sẵn sàng.")
        return False

    def _remix_cover_from_library(self, song_id: str) -> None:
        """Dự phòng: bài gốc đã lên thư viện Suno → ⋯ → Remix → Cover."""
        self._log("Form chưa vào chế độ Cover — thử ⋯ → Remix → Cover trên bài vừa tải lên.")
        self._ensure_song_open(song_id)
        # Save as New Song trả ID ngay khi hàng bài xuất hiện, nhưng mục Cover
        # còn bị khoá trong lúc trang báo “Preparing song for playback…”. Chờ
        # render thật sự hoàn tất trước khi mở submenu; nếu không click vào mục
        # disabled sẽ không điều hướng và workflow dừng giả.
        self._wait_preparing_done(timeout_sec=300)
        self._open_song_menu(song_id)
        remix = query_first(self.page, [
            "[role=menu] div.hxc-menu-item:has-text('Remix')",
            "[role=menu] div.context-menu-item:has-text('Remix')",
            "[role=menu] [role=menuitem]:has-text('Remix')"], timeout_ms=4000)
        if remix is None:
            raise self._ui_changed("Menu ⋯ của bài tải lên không có «Remix».", "no_remix")
        cover = None
        for how in ("hover", "click", "key"):
            try:
                if how == "hover":
                    if not self._hover_real(remix):
                        remix.hover()
                elif how == "click":
                    remix.click()
                else:
                    remix.focus()
                    self.page.keyboard.press("ArrowRight")
            except Exception:
                continue
            self._wait(700)
            cover = query_first(self.page, [
                "[role=menu] div.hxc-menu-item:has-text('Cover')",
                "[role=menu] div.context-menu-item:has-text('Cover')",
                "[role=menu] [role=menuitem]:has-text('Cover')"], timeout_ms=2000)
            if cover is not None:
                break
        if cover is None:
            raise self._ui_changed("Submenu Remix không có «Cover».", "no_remix_cover")
        before_url = self.page.url or ""
        self._click(cover)
        self._log("Đã bấm Remix → Cover — chờ Suno gắn audio vào form Create.")
        deadline = time.time() + 20
        while time.time() < deadline:
            self._wait(800)
            url = self.page.url or ""
            if "/create" in url or self.cover_ready() or self._top_dialog():
                self._log(f"Sau Remix → Cover: {before_url} → {url}.")
                self.save_debug("after_remix_cover")
                return
        raise self._ui_changed(
            "Đã bấm Remix → Cover nhưng Suno không chuyển sang form Create; "
            "tool không tự điều hướng để tránh làm mất audio nguồn.",
            "remix_cover_no_navigation")

    def attach_original(self, path: Path, human_wait_sec: float = 1800) -> None:
        """Đính file bài gốc vào form Create và chuyển sang chế độ Cover.
        Không xác nhận được chế độ Cover → SunoUIChanged (KHÔNG Create)."""
        path = Path(path)
        if not path.exists():
            raise SunoError(f"Không thấy file bài gốc: {path}")
        before = self.snapshot_song_ids()
        how = self._pick_file(path)
        self._log(f"Đã chọn file bài gốc «{path.name}» ({how}) — chờ Suno xử lý…")
        self._wait(2000)
        human = {"since": None, "nag": 0.0}
        if self._settle(150, human, human_wait_sec):
            self._log("Form đã ở chế độ Cover (thấy Audio Influence).")
            return
        fresh = [i for i in self.snapshot_song_ids() if i not in before]
        if len(fresh) == 1:
            self._remix_cover_from_library(fresh[0])
            if self._settle(60, human, human_wait_sec):
                self._log("Form đã ở chế độ Cover (qua Remix → Cover).")
                return
        raise self._ui_changed(
            "Không đưa được form Suno vào chế độ Cover (không thấy thanh «Audio "
            "Influence») — tool KHÔNG bấm Create. Gửi ảnh chụp này để cập nhật tool.",
            "no_cover_mode")

    # ── trọn bộ form cho 1 bài ───────────────────────────────────
    def prepare_cover(self, original: Path, title: str, lyrics: str,
                      gender: str, min_credits: int,
                      editor_bpm: int = EDITOR_TEMPO_SLOW,
                      editor_song_id: str = "",
                      styles: str = "",
                      on_editor_saved: Optional[Callable[[str], None]] = None,
                      manual_editor: bool = False,
                      alert: Optional[Callable[[str], None]] = None,
                      manual_wait_sec: float = 1800,
                      ) -> Optional[int]:
        """Upload → Editor → Save as New Song → Remix/Cover → điền form.

        Chỉ khi đã xác nhận lại chế độ Cover mới tìm Title/Lyrics, vì màn upload
        của Suno hiện tại không có các ô này.
        """
        self.open_create_page()
        credits = self.read_credits()
        if credits is not None and credits < min_credits:
            from backend.video.suno_driver import SunoBudgetError
            raise SunoBudgetError(
                f"Suno chỉ còn {credits} credit (mỗi bài cover cần ~{min_credits}). "
                "Tool không tự mua thêm — nạp/chờ credit rồi chạy lại.")
        self.select_advanced_mode()
        self.clear_form()
        self.ensure_more_options()
        self.ensure_duration_auto()         # có thể tải lại trang → làm TRƯỚC khi đính file
        source_song_id = (editor_song_id or "").strip()
        if source_song_id:
            self._log(f"Tiếp tục từ bài Editor đã lưu ({source_song_id}); không upload lại.")
        else:
            if manual_editor:
                source_song_id = self.wait_user_editor_save(
                    original, editor_bpm, alert=alert, timeout_sec=manual_wait_sec)
            else:
                source_song_id = self.prepare_editor_source(original, editor_bpm)
            if on_editor_saved is not None:
                on_editor_saved(source_song_id)
        self._remix_cover_from_library(source_song_id)
        human = {"since": None, "nag": 0.0}
        if not self._settle(90, human, 1800):
            raise self._ui_changed(
                "Đã Save as New Song nhưng Remix → Cover chưa mở được form có "
                "Audio Influence — tool KHÔNG bấm Create.", "editor_to_cover_failed")
        self.select_model(COVER_MODEL, allow_fallback=False)
        if not styles.strip():
            raise SunoError(
                "Chưa có Styles cho cover — không Create để tránh kế thừa style bài Editor.")
        self.fill_styles(styles)
        self.fill_title(title)
        self.fill_song_lyrics(lyrics)
        self.ensure_more_options()
        if gender in ("male", "female"):
            self.set_vocal_gender(gender)
        else:
            self._log("Chưa biết giọng nam/nữ — để Suno tự chọn.")
        self.set_max_mode(True)
        self.set_creative_sliders(COVER_SLIDERS)
        self.check_rejection()
        if not self.cover_ready():
            raise self._ui_changed("Form rời chế độ Cover sau khi chỉnh — KHÔNG Create.",
                                   "lost_cover_mode")
        self.save_debug("form_ready")
        return credits
