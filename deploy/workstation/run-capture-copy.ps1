# Scheduled-task wrapper for capture_copy.py (ADR-0013 amendment 6, threat T17).
# Template: the owner fills in the four values below in the copy in the admin-only job folder.
# Exit codes: the job's own (0 done, 2 refused or ignored files, 1 error), 3 hash mismatch,
# 4 interpreter or job file missing. Every non-zero exit must raise the owner's alert.

$ErrorActionPreference = 'Stop'

$Job      = '<job folder>\capture_copy.py'
$Expected = '<SHA-256 of capture_copy.py at the reviewed merge commit>'
$Python   = '<all-users python.exe under Program Files>'
$Folders  = '<job folder>\capture-folders.txt'
$Intake   = '<Greenlight intake root>'
$Log      = '<log folder>\capture-copy.log'

if (-not (Test-Path -LiteralPath $Job -PathType Leaf) -or -not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    exit 4
}
if ((Get-FileHash -LiteralPath $Job -Algorithm SHA256).Hash -ne $Expected.ToUpperInvariant()) {
    exit 3
}

& $Python -I -S $Job --folders $Folders --intake $Intake --log $Log
exit $LASTEXITCODE
