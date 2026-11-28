# Actual Docker-backed verifier demo for a preimplemented deterministic application.
param(
    [Parameter(Mandatory = $true)][string]$Image,
    [switch]$FailAcceptance,
    [switch]$FixtureApprovals
)

$ErrorActionPreference = 'Stop'
$env:PYTHONPATH = Join-Path $PWD 'src'
$root = Join-Path ([System.IO.Path]::GetTempPath()) ('capstone-actual-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$store = Join-Path $root 'workflow.db'
$config = Join-Path $root 'policy.yaml'
$python = if (Test-Path '.venv/Scripts/python.exe') { '.venv/Scripts/python.exe' } else { 'python' }
$inside = '/usr/local/bin/python'
$pytest = @($inside, '-P', '-m', 'pytest', '-q', '-c', '/dev/null', '-p', 'no:cacheprovider')
$security = @'
import subprocess
import sys

bandit = [sys.executable, '-I', '-m', 'bandit', '-q', '-r']
production = subprocess.call(bandit + ['-x', '/artifact/tests', '/artifact'])
tests = subprocess.call(bandit + ['-s', 'B101', '/artifact/tests'])
sys.exit(production or tests)
'@
@{
    jira = @{ issues_file = (Resolve-Path 'examples/enterprise/issues.json').Path }
    enterprise = @{
        approvers = @{ reviewer = 'CAPSTONE_FIXTURE_APPROVER' }
        verifiers = @{ verifier = 'CAPSTONE_FIXTURE_VERIFIER' }
        container = @{
            image = $Image
            commands = @{
                acceptance = $pytest + @('/acceptance')
                generated = $pytest + @('/artifact/tests/test_demo_1.py')
                regression = $pytest + @('/artifact/tests/test_regression.py')
                quality = @($inside, '-I', '-m', 'ruff', 'check', '--isolated', '--no-cache', '/artifact')
                security = @($inside, '-I', '-c', $security)
                definition_of_done = @($inside, '-I', '-c', 'from pathlib import Path; assert Path("tests/test_demo_1.py").is_file()')
                dev = @($inside, '-P', '-c', 'from calculator import subtract; assert subtract(2, 7) == -5')
            }
        }
    }
} | ConvertTo-Json -Depth 12 | Set-Content -Encoding ascii $config
$env:CAPSTONE_FIXTURE_APPROVER = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
$env:CAPSTONE_FIXTURE_VERIFIER = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
function Invoke-Dev {
    $data = & $python -B -m agentic_sdlc dev @args
    if ($LASTEXITCODE -ne 0) { throw "Offline CLI failed at $($args[0])" }
    $data | ConvertFrom-Json
}
function Confirm-Digest([string]$Stage, [string]$Digest, $Evidence) {
    if ($FixtureApprovals) { return }
    $evidenceFile = Join-Path $root "$Stage.json"
    $Evidence | ConvertTo-Json -Depth 30 | Set-Content -Encoding utf8 $evidenceFile
    Write-Host "Inspect $evidenceFile. Its approval digest is $Digest."
    if ((Read-Host "Enter the exact digest to approve $Stage") -cne $Digest) {
        throw "$Stage approval was not granted. The workflow and evidence are in $root"
    }
}
$draft = Invoke-Dev spec DEMO-1 --config $config --store $store
$common = @('--store', $store, '--run-id', $draft.run_id, '--config', $config)
Confirm-Digest 'requirement' $draft.draft.spec_sha256 $draft.draft
$null = Invoke-Dev approve @common --spec-sha256 $draft.draft.spec_sha256 --actor reviewer
$null = Invoke-Dev run @common --repo examples/enterprise/actual-fixture --out (Join-Path $root 'artifacts')
$acceptance = if ($FailAcceptance) { 'examples/enterprise/acceptance-failing' } else { 'examples/enterprise/acceptance' }
$raw = & $python -B -m agentic_sdlc dev testing @common --actor verifier --acceptance-dir $acceptance
$testingExit = $LASTEXITCODE
if ($testingExit -ne $(if ($FailAcceptance) { 2 } else { 0 })) { throw "Unexpected verifier result: $testingExit" }
$verification = $raw | ConvertFrom-Json
if ($FailAcceptance) {
    if ($verification.status -ne 'BLOCKED' -or
        ($verification.checks | Where-Object check_id -eq 'acceptance').result -ne 'FAILED') {
        throw 'The actual acceptance failure was not recorded'
    }
    $validation = 'BLOCKED'
} else {
    $review = Invoke-Dev review @common
    Confirm-Digest 'review' $review.review_sha256 $review
    $null = Invoke-Dev approve @common --review-sha256 $review.review_sha256 --actor reviewer
    $validation = (Invoke-Dev validate @common).status
}
$score = Invoke-Dev score @common --report-dir (Join-Path $root 'reports')
if ($FailAcceptance -and $score.delivery_eligibility -ne 'BLOCKED') {
    throw 'The score did not preserve the mandatory acceptance failure'
}
if (!$FailAcceptance -and $validation -ne 'LOCAL_DEV_VALIDATED') {
    throw "Unexpected validation status: $validation"
}
[pscustomobject]@{
    validation = $validation
    evidence = $verification.environment
    approval_mode = $(if ($FixtureApprovals) { 'FIXTURE' } else { 'INTERACTIVE' })
    checks = ($verification.checks | ForEach-Object { "$($_.check_id)=$($_.result)" }) -join ', '
    scoring = "$($score.earned_points)/$($score.configured_maximum) $($score.status) $($score.delivery_eligibility)"
    output = $root
}
