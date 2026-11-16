#!/bin/bash
# ============================================================
#  COVER AUDIO TOOL - CAP NHAT CHO MAC (patch) - KHONG can xoa ban cu
#  Tai ban moi nhat tu GitHub, chep de len thu muc tool.
#  GIU NGUYEN: .venv, data (setting + app.db), media, outputs,
#  stems, Projects, Trash, .env, dang nhap Suno/nhaccuatui.
#  Bam dup file nay (dat trong thu muc tool, canh start_mac.command).
# ============================================================
# Tu chep chinh minh ra thu muc tam roi chay ban sao, vi ban cap nhat
# se ghi de len file nay trong luc dang chay.
if [ "$1" != "--run" ]; then
    TOOL_DIR="$(cd "$(dirname "$0")" && pwd)"
    cp "$0" "${TMPDIR:-/tmp}/masteraudio_update.sh"
    exec bash "${TMPDIR:-/tmp}/masteraudio_update.sh" --run "$TOOL_DIR"
fi
cd "$2" || exit 1

pause_exit() {
    echo
    read -r -p "Nhan Enter de dong cua so..." _
    exit "${1:-0}"
}

echo
echo "============================================================"
echo "  COVER AUDIO TOOL - Dang cap nhat..."
echo "  Thu muc tool: $PWD"
echo "============================================================"
echo

[ -f backend/main.py ] || { echo "[LOI] File nay phai nam trong thu muc tool (canh start_mac.command)."; pause_exit 1; }
[ -x .venv/bin/python ] || { echo "[LOI] May chua cai tool. Hay chay install_mac.command truoc."; pause_exit 1; }

if lsof -nP -iTCP:8200 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[DUNG] Tool dang chay (cong 8200)."
    echo "  Doi cac viec dang chay xong, DONG cua so start_mac.command, roi bam lai file nay."
    pause_exit 1
fi

WORK="${TMPDIR:-/tmp}/masteraudio_update"
rm -rf "$WORK" && mkdir -p "$WORK"

echo "[1/3] Tai ban moi nhat tu GitHub..."
curl -fsSL -o "$WORK/main.zip" \
    "https://github.com/lucasstudio1228/MasterAudio/archive/refs/heads/main.zip" \
    || { echo "[LOI] Khong tai duoc. Kiem tra mang Internet roi thu lai."; pause_exit 1; }

echo "[2/3] Giai nen..."
unzip -q "$WORK/main.zip" -d "$WORK/x" || { echo "[LOI] Giai nen that bai."; pause_exit 1; }
SRC="$WORK/x/MasterAudio-main"
[ -f "$SRC/patch/removed_files.txt" ] || { echo "[LOI] File tai ve khong dung dinh dang."; pause_exit 1; }

echo "[3/3] Chep ban moi vao thu muc tool..."
STAMP=$(date +%Y%m%d_%H%M%S)
BK="_ban_cu_truoc_cap_nhat/$STAMP"
echo "  - Sao luu ma nguon cu vao $BK"
mkdir -p "$BK"
rsync -a --exclude '__pycache__' --exclude '/frontend/node_modules/' backend frontend "$BK/" 2>/dev/null
cp -p ./*.py ./*.command ./*.bat ./*.txt ./*.md "$BK/" 2>/dev/null

rm -rf frontend/dist
rsync -a --exclude '/data/' --exclude '/.git/' "$SRC/" ./ || { echo "[LOI] Chep file that bai."; pause_exit 1; }
# data: chi them file setting con thieu, KHONG ghi de setting/app.db cua may.
rsync -a --ignore-existing "$SRC/data/" ./data/

removed=0
while IFS= read -r rel || [ -n "$rel" ]; do
    rel="${rel%$'\r'}"
    case "$rel" in ''|'#'*|*..*) continue ;; backend/*|frontend/src/*) ;; *) continue ;; esac
    if [ -f "$rel" ]; then rm -f "$rel"; removed=$((removed + 1)); fi
done < "$SRC/patch/removed_files.txt"
echo "  - Da xoa $removed file cu khong con dung"
chmod +x ./*.command 2>/dev/null

echo "  - Kiem tra thu vien Python..."
VPY=".venv/bin/python"
ARCH=$("$VPY" -c 'import platform; print(platform.machine())')
if [ "$ARCH" = "x86_64" ]; then
    grep -viE '^demucs' requirements.txt > .req-mac-intel.tmp
    "$VPY" -m pip install -q --prefer-binary -c constraints-mac-intel.txt -r .req-mac-intel.tmp
    rc=$?
    rm -f .req-mac-intel.tmp
else
    "$VPY" -m pip install -q -r requirements.txt
    rc=$?
fi
[ $rc -eq 0 ] || { echo "[LOI] Cai thu vien that bai. Chup man hinh gui nguoi ho tro."; pause_exit 1; }

rm -rf "$WORK"
echo
echo "============================================================"
echo "  CAP NHAT XONG! Bam start_mac.command de mo lai Cover Audio Tool."
echo "============================================================"
pause_exit 0
