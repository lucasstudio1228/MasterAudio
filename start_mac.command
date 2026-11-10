#!/bin/bash
# ============================================================
#  COVER AUDIO TOOL - CHAY TREN MAC (bam dup de chay)
#  Chay server tren cong 8200 va tu mo trinh duyet.
# ============================================================
cd "$(dirname "$0")" || exit 1

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
# Homebrew (Apple Silicon / Intel) de tim thay ffmpeg khi mo tu Finder.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

if [ ! -x ".venv/bin/python" ]; then
    echo "[LOI] Chua cai dat. Hay bam dup install_mac.command truoc."
    read -r -p "Nhan Enter de dong cua so..." _
    exit 1
fi

echo
echo "============================================================"
echo "  COVER AUDIO TOOL dang khoi dong..."
echo "  Trinh duyet se tu mo tai: http://localhost:8200"
echo "  (De DONG ung dung: dong cua so Terminal nay hoac nhan Ctrl+C)"
echo "============================================================"
echo

( sleep 4; open "http://localhost:8200" ) &

.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8200
