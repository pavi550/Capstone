# Synthetic operator workflow. Run from the Capstone checkout.
$ErrorActionPreference = 'Stop'
$env:PYTHONPATH = Join-Path $PWD 'src'
$demo = Join-Path ([System.IO.Path]::GetTempPath()) ('capstone-lifecycle-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $demo | Out-Null
$store = Join-Path $demo 'workflow.db'
$config = Join-Path $demo 'offline.yaml'
$fixture = (Resolve-Path 'examples/enterprise/issues.json').Path.Replace('\', '/')
@"
jira:
  issues_file: "$fixture"
enterprise:
  approvers:
    reviewer: CAPSTONE_DEMO_APPROVER
  verifiers:
    verifier: CAPSTONE_DEMO_VERIFIER
"@ | Set-Content -Encoding ascii $config
$env:CAPSTONE_DEMO_APPROVER = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
$env:CAPSTONE_DEMO_VERIFIER = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
$python = if (Test-Path '.venv/Scripts/python.exe') { '.venv/Scripts/python.exe' } else { 'python' }
function Invoke-Dev {
    $data = & $python -B -m agentic_sdlc dev @args
    if ($LASTEXITCODE -ne 0) { throw "Offline CLI failed at $($args[0])" }
    $data | ConvertFrom-Json
}
$spec = Invoke-Dev spec DEMO-1 --config $config --store $store
$common = @('--store', $store, '--config', $config, '--run-id', $spec.run_id)
$approved = Invoke-Dev approve @common --spec-sha256 $spec.draft.spec_sha256 --actor reviewer
$out = Join-Path $demo 'artifacts'
$artifact = Invoke-Dev run @common --repo examples/sample_repo --out $out
$again = Invoke-Dev resume @common --repo examples/sample_repo --out $out
if ($artifact.manifest_sha256 -ne $again.manifest_sha256) { throw 'Resume produced different artifacts' }
$result = Join-Path $demo 'synthetic-verifier.json'
@{
    run_id = $spec.run_id
    spec_sha256 = $spec.draft.spec_sha256
    manifest_sha256 = $artifact.manifest_sha256
    environment = 'OFFLINE_SIMULATION'
    checks = @{ acceptance = $true; regression = $true; security = $true
        guardrails = $true; definition_of_done = $true; dev = $true }
} | ConvertTo-Json | Set-Content -Encoding ascii $result
$verified = Invoke-Dev verify @common --actor verifier --result $result
$review = Invoke-Dev review @common
$null = Invoke-Dev approve @common --review-sha256 $review.review_sha256 --actor reviewer
$validated = Invoke-Dev validate @common
$report = Invoke-Dev report --store $store --run-id $spec.run_id --output (Join-Path $demo 'lifecycle-report.json')
[pscustomobject]@{
    draft = $spec.draft.status
    approval = $approved.status
    development = $artifact.status
    verification = $verified.status
    review = $review.status
    validation = $validated.status
    attempts = $report.development_attempts.Count
    output = $demo
}
