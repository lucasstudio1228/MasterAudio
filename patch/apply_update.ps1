# Cover Audio Tool - chep ban moi (Src) de len thu muc tool (Dst).
# Goi tu update.bat. Khong dung toi .venv, data (chi them file con thieu),
# media, outputs, stems, Projects, Trash, .env, .browser_profile*.
param(
    [Parameter(Mandatory = $true)][string]$Src,
    [Parameter(Mandatory = $true)][string]$Dst
)
$ErrorActionPreference = 'Stop'

function Copy-Tree([string]$From, [string]$To, [string[]]$Extra) {
    # robocopy: ma thoat < 8 la thanh cong. Khong dung /PURGE -> khong xoa file rieng cua may.
    $rc = @($From, $To, '/E', '/R:1', '/W:1', '/NFL', '/NDL', '/NJH', '/NJS', '/NP') + $Extra
    & robocopy @rc | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy loi ($LASTEXITCODE): $From -> $To" }
}

# 1) Sao luu ma nguon cu (nho, de khoi phuc neu can).
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$backup = Join-Path $Dst "_ban_cu_truoc_cap_nhat\$stamp"
Write-Host "  - Sao luu ma nguon cu vao _ban_cu_truoc_cap_nhat\$stamp"
Copy-Tree (Join-Path $Dst 'backend') (Join-Path $backup 'backend') @('/XD', '__pycache__')
Copy-Tree (Join-Path $Dst 'frontend') (Join-Path $backup 'frontend') @('/XD', (Join-Path $Dst 'frontend\node_modules'))
Get-ChildItem -LiteralPath $Dst -File | Where-Object { $_.Extension -in '.py', '.bat', '.command', '.txt', '.md' } |
    ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $backup }

# 2) Giao dien da build: thay ca thu muc cho sach file cu.
$dist = Join-Path $Dst 'frontend\dist'
if (Test-Path -LiteralPath $dist) { Remove-Item -LiteralPath $dist -Recurse -Force }

# 3) Chep ma nguon moi (tru data va .git).
Write-Host "  - Chep ban moi"
Copy-Tree $Src $Dst @('/XD', (Join-Path $Src 'data'), (Join-Path $Src '.git'))

# 4) data: chi them file setting con thieu, KHONG ghi de setting/app.db cua may.
Copy-Tree (Join-Path $Src 'data') (Join-Path $Dst 'data') @('/XC', '/XN', '/XO')

# 5) Xoa file cu khong con dung (phan video da go bo).
$list = Join-Path $Src 'patch\removed_files.txt'
$removed = 0
if (Test-Path -LiteralPath $list) {
    foreach ($line in Get-Content -LiteralPath $list -Encoding UTF8) {
        $rel = $line.Trim()
        if (-not $rel -or $rel.StartsWith('#')) { continue }
        if ($rel.Contains('..') -or -not ($rel.StartsWith('backend/') -or $rel.StartsWith('frontend/src/'))) { continue }
        $p = Join-Path $Dst ($rel -replace '/', '\')
        if (Test-Path -LiteralPath $p -PathType Leaf) { Remove-Item -LiteralPath $p -Force; $removed++ }
    }
}
Write-Host "  - Da xoa $removed file cu khong con dung"

# 6) Cai thu vien moi - chi khi requirements.txt thay doi so voi ban cu.
$oldReq = Join-Path $backup 'requirements.txt'
$newReq = Join-Path $Dst 'requirements.txt'
function Read-Norm([string]$p) { ((Get-Content -LiteralPath $p -Raw -Encoding UTF8) -replace "`r", '').Trim() }
if ((Test-Path -LiteralPath $oldReq) -and ((Read-Norm $oldReq) -ceq (Read-Norm $newReq))) {
    Write-Host "  - Thu vien Python khong doi - bo qua"
} else {
Write-Host "  - Cai thu vien Python moi..."
$py = Join-Path $Dst '.venv\Scripts\python.exe'
& $py -m pip install -q -r (Join-Path $Dst 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw "pip install loi ($LASTEXITCODE)" }
}

# 7) File .exe mo tool trong cua so ung dung + loi tat Desktop (loi o day khong chan cap nhat).
$setup = Join-Path $Dst 'launcher_exe\setup_app.ps1'
if (Test-Path -LiteralPath $setup) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $setup -Dir $Dst
}
exit 0
