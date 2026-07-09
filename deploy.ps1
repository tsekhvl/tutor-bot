# Deploy Tutor Bot to NL VPS
# Run: deploy.bat (or powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy.ps1)

$ErrorActionPreference = "Stop"
$SERVER = "root@YOUR_VPS_HOST"
$REMOTE_PATH = "/opt/tutor_bot"

$FILES = @(
    "main.py",
    "config.py",
    "requirements.txt",
    "assignments.json",
    "bot",
    "ai",
    "sheets",
    "storage",
    "control",
    "exam_train",
    "data",
    "tutor-bot.service",
    "setup-server.sh",
    "DEPLOY.md",
    ".env.example"
)

Write-Host "Uploading to ${SERVER}:${REMOTE_PATH} ..." -ForegroundColor Cyan
ssh $SERVER "mkdir -p $REMOTE_PATH/data"

$toUpload = @()
foreach ($f in $FILES) {
    if (Test-Path $f) {
        $toUpload += (Resolve-Path $f).Path
    } else {
        Write-Host "Skip (not found): $f" -ForegroundColor Yellow
    }
}
if (Test-Path "my-project-key.json") {
    $toUpload += (Resolve-Path "my-project-key.json").Path
} else {
    Write-Host "Skip: my-project-key.json (upload manually, see DEPLOY.md)" -ForegroundColor Yellow
}

if ($toUpload.Count -gt 0) {
    Write-Host "Upload batch: $($toUpload.Count) paths (one scp)" -ForegroundColor DarkGray
    & scp -r @toUpload "${SERVER}:${REMOTE_PATH}/"
}

Write-Host ""
Write-Host "pip + restart (if service exists)..." -ForegroundColor Cyan
ssh $SERVER @"
cd $REMOTE_PATH && \
if [ -d venv ]; then . venv/bin/activate && pip install -r requirements.txt -q; fi && \
if systemctl is-active --quiet tutor-bot 2>/dev/null; then \
  systemctl restart tutor-bot && systemctl status tutor-bot -l --no-pager; \
else \
  echo 'Service tutor-bot not created yet - see DEPLOY.md step 6.'; \
fi
"@

Write-Host ""
Write-Host "Done." -ForegroundColor Green
