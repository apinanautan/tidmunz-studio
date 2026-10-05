param(
    [Parameter(Mandatory=$true)][string]$Version,
    [string]$Notes = "อัปเดตและแก้ไขความเสถียร"
)

$ErrorActionPreference = "Stop"
$Tools = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $Tools
$Repo = "apinanautan/tidmunz-studio"
$Version = $Version.Trim().TrimStart("v")
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "เลขเวอร์ชันต้องเป็นรูปแบบ 1.0.1"
}

& gh auth status 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "ยังไม่ได้ Login GitHub CLI — รัน gh auth login เพียงครั้งเดียว แล้วเปิดสคริปต์นี้ใหม่"
}

$versionPath = Join-Path $Root "snapgen_data\meta\snapgen_version.json"
$originalVersionJson = [System.IO.File]::ReadAllText($versionPath)
$versionData = Get-Content -LiteralPath $versionPath -Raw | ConvertFrom-Json
$currentVersion = [version]([string]$versionData.version)
$requestedVersion = [version]$Version
if ($requestedVersion -le $currentVersion) {
    throw "เวอร์ชันใหม่ต้องมากกว่า v$currentVersion — กรุณาใช้ตัวอย่างเช่น $($currentVersion.Major).$($currentVersion.Minor).$($currentVersion.Build + 1)"
}
$versionData.version = $Version
$json = $versionData | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText($versionPath, $json + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding($false)))
$published = $false

try {
    $python = Join-Path $Root ".venv312\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) {
        $python = (Get-Command python -ErrorAction Stop).Source
    }
    & $python -c "import PyInstaller" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "กำลังติดตั้ง PyInstaller สำหรับแพ็กเกจ Windows EXE..."
        & $python -m pip install --disable-pip-version-check --no-input pyinstaller
        if ($LASTEXITCODE -ne 0) { throw "ติดตั้ง PyInstaller ไม่สำเร็จ" }
    }
    & $python -B (Join-Path $Tools "build_update_patch.py")
    if ($LASTEXITCODE -ne 0) { throw "สร้าง Patch ไม่สำเร็จ" }
    $asset = Join-Path $Tools "release\tidmun-studio-patch.zip"
    & $python -B (Join-Path $Tools "build_launcher.py")
    if ($LASTEXITCODE -ne 0) { throw "สร้าง Tidmunz Studio.exe ไม่สำเร็จ" }
    $exeAsset = Join-Path $Tools "release\Tidmunz Studio.exe"
    if (-not (Test-Path -LiteralPath $exeAsset)) { throw "ไม่พบ Tidmunz Studio.exe" }

    # An empty GitHub repository needs one initial commit before its first tag.
    $repoInfo = & gh api "repos/$Repo" | ConvertFrom-Json
    # GitHub can report default_branch="main" even while an empty repository has
    # no commit.  A Release tag cannot be created until that first commit exists.
    $null = & cmd.exe /d /c "gh api repos/$Repo/git/ref/heads/main >nul 2>nul"
    $hasMainCommit = ($LASTEXITCODE -eq 0)
    if (-not $hasMainCommit) {
        Write-Host "Repository ยังว่าง — กำลังสร้าง commit แรกผ่าน GitHub API..."
        $readme = "# ติดมันส์ สตูดิโอ Updates`n`nRepository สำหรับแจก Patch อัตโนมัติ ไม่มี Account, Cookie, Context หรือไฟล์งานผู้ใช้`n"
        $readmeBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($readme))
        & gh api --method PUT "repos/$Repo/contents/README.md" -f "message=Initialize update repository" -f "content=$readmeBase64" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "สร้าง commit แรกผ่าน GitHub API ไม่สำเร็จ" }
        Write-Host "สร้าง commit แรกสำเร็จ"
    }

    $existingTags = @(& gh release list --repo $Repo --limit 100 --json tagName --jq '.[].tagName')
    if ($LASTEXITCODE -ne 0) { throw "ตรวจรายการ Release เดิมไม่สำเร็จ" }
    if ($existingTags -contains "v$Version") {
        throw "มี Release v$Version อยู่แล้ว กรุณาใช้เลขเวอร์ชันใหม่"
    }
    & gh release create "v$Version" $asset $exeAsset --repo $Repo --title "ติดมันส์ สตูดิโอ v$Version" --notes $Notes --latest
    if ($LASTEXITCODE -ne 0) { throw "เผยแพร่ GitHub Release ไม่สำเร็จ" }
    $published = $true
}
finally {
    if (-not $published) {
        [System.IO.File]::WriteAllText($versionPath, $originalVersionJson, (New-Object System.Text.UTF8Encoding($false)))
    }
}

Write-Host "เผยแพร่สำเร็จ: https://github.com/$Repo/releases/tag/v$Version" -ForegroundColor Green
