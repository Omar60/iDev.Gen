[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$DiffBase
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repository = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$workspace = [IO.Path]::GetFullPath($env:IDEVGEN_A3_TEMP)
$runnerTemp = [IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\')
$tempRoot = Join-Path $workspace 'temp'
$logs = Join-Path $workspace 'logs'
$traces = Join-Path $workspace 'traces'
$node = (Get-Command node -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$python = Join-Path $repository '.venv\Scripts\python.exe'
$playwright = Join-Path $repository 'frontend\node_modules\@playwright\test\cli.js'
$browserRoot = [IO.Path]::GetFullPath((Join-Path $repository 'frontend\node_modules\playwright-core\.local-browsers')).TrimEnd('\')
$browserProfilePrefix = [IO.Path]::GetFullPath($tempRoot).TrimEnd('\') + '\playwright_chromiumdev_profile-'
$utf8 = [Text.UTF8Encoding]::new($false)

if (-not $workspace.StartsWith("$runnerTemp\", [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The E2E workspace must be below RUNNER_TEMP.'
}
foreach ($path in @($tempRoot, $logs, $traces, $python, $playwright)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Required E2E path is missing: $path" }
}
if ($DiffBase -notmatch '^[0-9a-fA-F]{40}$') { throw 'The diff base must be a full Git commit SHA.' }

$chromiumNames = @(
    'chrome.exe', 'chromium.exe', 'chrome-headless-shell.exe', 'headless_shell.exe',
    'chrome_crashpad_handler.exe', 'crashpad_handler.exe'
)

function Get-E2ERootInventory {
    @(
        Get-ChildItem -LiteralPath $tempRoot -Force -ErrorAction Stop |
            Where-Object { $_.Name -like 'idevgen-e2e-*' } |
            ForEach-Object {
                $reparsePoint = [bool]($_.Attributes -band [IO.FileAttributes]::ReparsePoint)
                $marked = -not $reparsePoint -and $_.PSIsContainer -and
                    (Test-Path -LiteralPath (Join-Path $_.FullName '.idevgen-e2e-owner'))
                [pscustomobject]@{
                    name = $_.Name
                    marked = $marked
                    reparsePoint = $reparsePoint
                }
            }
    )
}

function Get-ChromiumInventory {
    @(
        Get-CimInstance -ClassName Win32_Process -ErrorAction Stop |
            Where-Object { $_.Name -in $chromiumNames } |
            ForEach-Object {
                if ($null -eq $_.CreationDate) { throw "Cannot establish creation identity for Chromium process $($_.ProcessId)." }
                [pscustomobject]@{
                    processId = [uint32]$_.ProcessId
                    parentProcessId = [uint32]$_.ParentProcessId
                    name = [string]$_.Name
                    creationTicks = $_.CreationDate.ToUniversalTime().Ticks
                    executablePath = [string]$_.ExecutablePath
                    commandLine = [string]$_.CommandLine
                }
            }
    )
}

function Assert-NoPreviousE2ERoots([string]$Phase) {
    $roots = @(Get-E2ERootInventory)
    if ($roots.Count -gt 0) {
        $names = ($roots | ForEach-Object { $_.name }) -join ', '
        throw "Refusing to start $Phase while an E2E root remains: $names. No cleanup was attempted."
    }
}

function Invoke-LoggedProcess {
    param(
        [string]$Name,
        [string]$Executable,
        [string[]]$Arguments,
        [string]$WorkingDirectory = $repository,
        [hashtable]$Environment = @{}
    )

    Assert-NoPreviousE2ERoots $Name
    $stdoutPath = Join-Path $logs "$Name.stdout.log"
    $stderrPath = Join-Path $logs "$Name.stderr.log"
    if ((Test-Path -LiteralPath $stdoutPath) -or (Test-Path -LiteralPath $stderrPath)) {
        throw "Refusing to overwrite existing $Name logs."
    }

    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $Executable
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($argument in $Arguments) { $startInfo.ArgumentList.Add([string]$argument) }
    foreach ($key in $Environment.Keys) { $startInfo.Environment[$key] = [string]$Environment[$key] }

    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    if (-not $process.Start()) { throw "Could not start phase $Name." }
    $processId = $process.Id
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    $exitCode = $process.ExitCode
    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    $process.Dispose()
    [IO.File]::WriteAllText($stdoutPath, $stdout, $utf8)
    [IO.File]::WriteAllText($stderrPath, $stderr, $utf8)
    $rootsAfter = @(Get-E2ERootInventory)

    Write-Host "PHASE $Name exit=$exitCode stdout=$stdoutPath stderr=$stderrPath"
    if ($stdout) { Write-Host $stdout.TrimEnd() }
    if ($stderr) { Write-Host $stderr.TrimEnd() }
    return [pscustomobject]@{
        name = $Name
        executable = $Executable
        processId = [uint32]$processId
        exitCode = $exitCode
        stdout = $stdout
        stderr = $stderr
        rootsAfter = $rootsAfter
    }
}

function Get-ChromiumIdentityFacts([object]$Process) {
    $binaryKnown = $false
    $binaryMatch = $false
    $profileKnown = $false
    $profileMatch = $false
    try {
        if (-not [string]::IsNullOrWhiteSpace($Process.executablePath)) {
            $executable = [IO.Path]::GetFullPath($Process.executablePath)
            $binaryKnown = $true
            $binaryMatch = $executable.StartsWith("$browserRoot\", [StringComparison]::OrdinalIgnoreCase)
        }
        $profile = [regex]::Match([string]$Process.commandLine, '--user-data-dir=(?<value>"[^"]+"|[^\s]+)')
        if ($profile.Success) {
            $profilePath = [IO.Path]::GetFullPath($profile.Groups['value'].Value.Trim('"'))
            $profileKnown = $true
            $profileMatch = $profilePath.StartsWith($browserProfilePrefix, [StringComparison]::OrdinalIgnoreCase)
        }
    } catch {
        $binaryKnown = $false
        $profileKnown = $false
        $binaryMatch = $false
        $profileMatch = $false
    }
    return [pscustomobject]@{
        binaryKnown = $binaryKnown
        playwrightBinary = $binaryMatch
        profileKnown = $profileKnown
        isolatedProfile = $profileMatch
    }
}

function Get-ChromiumOwnership([object]$Process, [hashtable]$BaselineByPid, [hashtable]$CurrentByPid) {
    $facts = Get-ChromiumIdentityFacts $Process
    $parentKnownOutsideHarness = $false
    $parentId = [uint32]$Process.parentProcessId
    for ($depth = 0; $depth -lt 16; $depth++) {
        $key = [string]$parentId
        if (-not $BaselineByPid.ContainsKey($key) -or -not $CurrentByPid.ContainsKey($key)) { break }
        $ancestor = $BaselineByPid[$key]
        $currentAncestor = $CurrentByPid[$key]
        if ($currentAncestor.creationTicks -ne $ancestor.creationTicks) { break }
        $ancestorFacts = Get-ChromiumIdentityFacts $ancestor
        if ($ancestorFacts.binaryKnown -and -not $ancestorFacts.playwrightBinary -and
            $ancestorFacts.profileKnown -and -not $ancestorFacts.isolatedProfile) {
            $parentKnownOutsideHarness = $true
            break
        }
        $parentId = [uint32]$ancestor.parentProcessId
    }

    if ($facts.playwrightBinary -and $facts.isolatedProfile) { $classification = 'harness' }
    elseif ($facts.playwrightBinary -or $facts.isolatedProfile) { $classification = 'uncertain' }
    elseif ($facts.binaryKnown -and $facts.profileKnown -or $parentKnownOutsideHarness) { $classification = 'unrelated' }
    else { $classification = 'uncertain' }
    return [pscustomobject]@{
        classification = $classification
        binaryKnown = $facts.binaryKnown
        playwrightBinary = $facts.playwrightBinary
        profileKnown = $facts.profileKnown
        isolatedProfile = $facts.isolatedProfile
        parentKnownOutsideHarness = $parentKnownOutsideHarness
    }
}

function Assert-ChromiumOwnershipSelfCheck {
    $baseline = @{}
    $profile = Join-Path $tempRoot 'playwright_chromiumdev_profile-synthetic'
    $harness = [pscustomobject]@{
        processId = [uint32]501; parentProcessId = [uint32]400; name = 'chrome.exe'
        executablePath = Join-Path $browserRoot 'chromium-synthetic\chrome.exe'
        commandLine = "chrome.exe --user-data-dir=`"$profile`""
    }
    $external = [pscustomobject]@{
        processId = [uint32]502; parentProcessId = [uint32]400; name = 'chrome.exe'
        executablePath = 'C:\synthetic\chrome.exe'
        commandLine = 'chrome.exe --user-data-dir=C:\synthetic\profile'
    }
    $uncertain = [pscustomobject]@{
        processId = [uint32]503; parentProcessId = [uint32]499; name = 'chrome.exe'
        executablePath = 'C:\synthetic\chrome.exe'; commandLine = 'chrome.exe --type=renderer'
    }
    $externalParent = [pscustomobject]@{
        processId = [uint32]504; parentProcessId = [uint32]400; creationTicks = 100; name = 'chrome.exe'
        executablePath = 'C:\synthetic\chrome.exe'
        commandLine = 'chrome.exe --user-data-dir=C:\synthetic\profile'
    }
    $baseline[[string]$externalParent.processId] = $externalParent
    $currentBaseline = @{ [string]$externalParent.processId = $externalParent }
    $externalChild = [pscustomobject]@{
        processId = [uint32]505; parentProcessId = $externalParent.processId; name = 'chrome.exe'
        executablePath = 'C:\synthetic\chrome.exe'; commandLine = 'chrome.exe --type=renderer'
    }
    $reusedParent = [pscustomobject]@{
        processId = [uint32]504; parentProcessId = [uint32]400; creationTicks = 200; name = 'chrome.exe'
        executablePath = 'C:\synthetic\chrome.exe'
        commandLine = 'chrome.exe --user-data-dir=C:\synthetic\profile'
    }
    $reusedPid = @{ [string]$reusedParent.processId = $reusedParent }
    if ((Get-ChromiumOwnership $harness @{} @{}).classification -ne 'harness' -or
        (Get-ChromiumOwnership $external @{} @{}).classification -ne 'unrelated' -or
        (Get-ChromiumOwnership $uncertain @{} @{}).classification -ne 'uncertain' -or
        (Get-ChromiumOwnership $externalChild $baseline $currentBaseline).classification -ne 'unrelated' -or
        (Get-ChromiumOwnership $externalChild $baseline $reusedPid).classification -ne 'uncertain') {
        throw 'Chromium ownership classification self-check failed.'
    }
}

function Wait-ForChromiumExit([object[]]$Baseline) {
    $baselineIds = @{}
    $baselineByPid = @{}
    foreach ($process in $Baseline) {
        $baselineIds["$($process.processId):$($process.creationTicks)"] = $true
        $baselineByPid[[string]$process.processId] = $process
    }
    $result = [pscustomobject]@{ harnessResiduals = @(); ownershipUncertain = @(); unrelatedNewProcessCount = 0 }
    for ($attempt = 0; $attempt -le 10; $attempt++) {
        $currentProcesses = @(Get-ChromiumInventory)
        $currentByPid = @{}
        foreach ($currentProcess in $currentProcesses) { $currentByPid[[string]$currentProcess.processId] = $currentProcess }
        $newProcesses = @($currentProcesses | Where-Object { -not $baselineIds.ContainsKey("$($_.processId):$($_.creationTicks)") })
        $harness = [Collections.Generic.List[object]]::new()
        $uncertain = [Collections.Generic.List[object]]::new()
        $unrelatedCount = 0
        foreach ($process in $newProcesses) {
            $ownership = Get-ChromiumOwnership $process $baselineByPid $currentByPid
            $receipt = [pscustomobject]@{
                processId = $process.processId
                parentProcessId = $process.parentProcessId
                name = $process.name
                ownership = $ownership.classification
                playwrightBinary = $ownership.playwrightBinary
                isolatedProfile = $ownership.isolatedProfile
                parentKnownOutsideHarness = $ownership.parentKnownOutsideHarness
            }
            if ($ownership.classification -eq 'harness') { $harness.Add($receipt) }
            elseif ($ownership.classification -eq 'uncertain') { $uncertain.Add($receipt) }
            else { $unrelatedCount++ }
        }
        $result = [pscustomobject]@{
            harnessResiduals = @($harness)
            ownershipUncertain = @($uncertain)
            unrelatedNewProcessCount = $unrelatedCount
        }
        if ($result.harnessResiduals.Count -eq 0 -and $result.ownershipUncertain.Count -eq 0) { return $result }
        if ($attempt -lt 10) { Start-Sleep -Seconds 1 }
    }
    return $result
}

function Write-HarnessAudit {
    param(
        [object]$Run,
        [bool]$CheckChromium,
        [object[]]$ChromiumBaseline
    )

    $issues = [Collections.Generic.List[string]]::new()
    $readyPattern = '(?m)^E2E_SERVER_READY base_url=(http://127\.0\.0\.1:(?<urlPort>\d+)) pid=(?<pid>\d+) parent_pid=(?<parent>\d+) port=(?<port>\d+) nonce=(?<nonce>[a-f0-9]{48}) root_id=(?<root>[a-f0-9]{64})$'
    $readyMatches = [regex]::Matches($Run.stdout, $readyPattern)
    $identity = $null
    $pidAbsent = $null
    $listenerAbsent = $null
    $remainingPid = @()
    $listeningSockets = @()
    if ($readyMatches.Count -ne 1) {
        $issues.Add("Expected one exact E2E_SERVER_READY identity; found $($readyMatches.Count).")
    } else {
        $match = $readyMatches[0]
        $serverPid = [uint32]$match.Groups['pid'].Value
        $parentPid = [uint32]$match.Groups['parent'].Value
        $port = [uint32]$match.Groups['port'].Value
        $urlPort = [uint32]$match.Groups['urlPort'].Value
        if ($port -lt 1 -or $port -gt 65535 -or $urlPort -ne $port) { $issues.Add('The readiness URL and bound port did not match.') }
        if ($parentPid -ne $Run.processId) { $issues.Add('The server parent PID did not match this phase launcher PID.') }
        $identity = [pscustomobject]@{
            baseUrl = $match.Groups[1].Value
            serverPid = $serverPid
            parentPid = $parentPid
            launcherPid = $Run.processId
            port = $port
            nonceLength = $match.Groups['nonce'].Value.Length
            rootTokenLength = $match.Groups['root'].Value.Length
        }
        if ($port -ge 1 -and $port -le 65535 -and $urlPort -eq $port -and $parentPid -eq $Run.processId) {
            try {
                $remainingPid = @(Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $serverPid" -ErrorAction Stop)
                $pidAbsent = $remainingPid.Count -eq 0
                if (-not $pidAbsent) { $issues.Add('The harness-owned server PID is still present in Win32_Process.') }
            } catch {
                $issues.Add("Win32_Process PID inspection failed: $($_.Exception.Message)")
            }
            try {
                $listeningSockets = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.LocalPort -eq $port })
                $listenerAbsent = $listeningSockets.Count -eq 0
                if (-not $listenerAbsent) { $issues.Add('The harness server port still has a listening socket.') }
            } catch {
                $issues.Add("Get-NetTCPConnection listener inspection failed: $($_.Exception.Message)")
            }
        }
    }

    $requiredMarkers = @(
        'E2E_HTML_PREFLIGHT clean=true',
        'E2E_SERVER_EXIT termination=graceful forced=false code=0 signal=none streams_drained=true',
        'E2E_SERVER_STOPPED clean=true',
        'E2E_ROOT_REMOVED clean=true'
    )
    $markerCounts = [ordered]@{}
    foreach ($marker in $requiredMarkers) {
        $count = [regex]::Matches($Run.stdout, [regex]::Escape($marker)).Count
        $markerCounts[$marker] = $count
        if ($count -ne 1) { $issues.Add("Expected one lifecycle marker '$marker'; found $count.") }
    }

    try {
        $remainingRoots = @(Get-E2ERootInventory)
        if ($remainingRoots.Count -gt 0) { $issues.Add('An E2E temporary root remains; it was preserved.') }
    } catch {
        $remainingRoots = @()
        $issues.Add("E2E root inspection failed: $($_.Exception.Message)")
    }
    if (@($Run.rootsAfter).Count -gt 0 -and $remainingRoots.Count -eq 0) {
        $remainingRoots = @($Run.rootsAfter)
        $issues.Add('A root observed at process exit no longer appears; no cleanup was attempted by this auditor.')
    }

    $chromiumAudit = [pscustomobject]@{ harnessResiduals = @(); ownershipUncertain = @(); unrelatedNewProcessCount = 0 }
    if ($CheckChromium) {
        try {
            $chromiumAudit = Wait-ForChromiumExit $ChromiumBaseline
            if ($chromiumAudit.harnessResiduals.Count -gt 0) { $issues.Add('Playwright Chromium or Crashpad processes remain after the pass.') }
            if ($chromiumAudit.ownershipUncertain.Count -gt 0) { $issues.Add('New Chromium process ownership could not be established; no process was touched.') }
        } catch {
            $issues.Add("Chromium process inspection failed: $($_.Exception.Message)")
        }
    }

    $receipt = [ordered]@{
        phase = $Run.name
        launcherExitCode = $Run.exitCode
        identity = $identity
        readinessCount = $readyMatches.Count
        lifecycleMarkers = $markerCounts
        serverPidAbsent = $pidAbsent
        listeningPortAbsent = $listenerAbsent
        remainingRoots = $remainingRoots
        chromiumResiduals = $chromiumAudit.harnessResiduals
        chromiumOwnershipUncertain = $chromiumAudit.ownershipUncertain
        unrelatedChromiumProcessCount = $chromiumAudit.unrelatedNewProcessCount
        issues = @($issues)
    }
    $receiptPath = Join-Path $logs "$($Run.name)-windows-audit.json"
    [IO.File]::WriteAllText($receiptPath, ($receipt | ConvertTo-Json -Depth 10), $utf8)
    Write-Host "WINDOWS_AUDIT $($receipt | ConvertTo-Json -Compress -Depth 10)"
    return [pscustomobject]@{ passed = $issues.Count -eq 0; issues = @($issues); receiptPath = $receiptPath }
}

function Get-ReportSpecs([object]$Suite, [Collections.Generic.List[object]]$Specs) {
    if ($Suite.PSObject.Properties['specs']) {
        foreach ($spec in @($Suite.specs)) { if ($null -ne $spec) { $Specs.Add($spec) } }
    }
    if ($Suite.PSObject.Properties['suites']) {
        foreach ($child in @($Suite.suites)) { if ($null -ne $child) { Get-ReportSpecs $child $Specs } }
    }
}

function Write-PlaywrightInventoryAudit([string]$Phase, [string]$ReportPath) {
    if (-not (Test-Path -LiteralPath $ReportPath)) { throw "$Phase Playwright JSON report was not created." }
    $report = Get-Content -LiteralPath $ReportPath -Raw | ConvertFrom-Json -Depth 100
    $specs = [Collections.Generic.List[object]]::new()
    foreach ($suite in @($report.suites)) { Get-ReportSpecs $suite $specs }
    $expected = [ordered]@{
        'smoke.e2e.mjs' = 1
        'model-detail.e2e.mjs' = 3
        'session-submit.e2e.mjs' = 5
        'session-prepare.e2e.mjs' = 4
        'session-guard.e2e.mjs' = 1
        'session-review.e2e.mjs' = 8
        'session-approval.e2e.mjs' = 8
    }
    $counts = @{}
    foreach ($name in $expected.Keys) { $counts[$name] = 0 }
    $attempts = [Collections.Generic.List[object]]::new()
    foreach ($spec in $specs) {
        $file = [IO.Path]::GetFileName(([string]$spec.file).Replace('/', '\'))
        if (-not $counts.ContainsKey($file)) {
            $attempts.Add([pscustomobject]@{ file = $file; title = $spec.title; testStatus = 'unknown file'; resultStatus = 'unknown file'; resultCount = 0 })
            continue
        }
        $counts[$file]++
        $tests = @($spec.tests)
        if ($tests.Count -ne 1) {
            $attempts.Add([pscustomobject]@{ file = $file; title = $spec.title; testStatus = 'wrong project count'; resultStatus = 'missing-or-retried'; resultCount = 0 })
            continue
        }
        $results = @($tests[0].results)
        $resultStatus = if ($results.Count -eq 1) { [string]$results[0].status } else { 'missing-or-retried' }
        $attempts.Add([pscustomobject]@{
            file = $file
            title = $spec.title
            testStatus = [string]$tests[0].status
            resultStatus = $resultStatus
            resultCount = $results.Count
        })
        if ($tests[0].status -ne 'expected' -or $results.Count -ne 1 -or $resultStatus -ne 'passed') {
            continue
        }
    }
    $expectedStats = [bool]($report.stats.expected -eq 30 -and $report.stats.skipped -eq 0 -and $report.stats.unexpected -eq 0 -and $report.stats.flaky -eq 0)
    $errorsEmpty = @($report.errors).Count -eq 0
    $countsExact = $specs.Count -eq 30
    foreach ($name in $expected.Keys) { if ($counts[$name] -ne $expected[$name]) { $countsExact = $false } }
    $allPassed = $attempts.Count -eq 30 -and @($attempts | Where-Object { $_.testStatus -ne 'expected' -or $_.resultStatus -ne 'passed' -or $_.resultCount -ne 1 }).Count -eq 0
    $smokePassed = @($attempts | Where-Object { $_.file -eq 'smoke.e2e.mjs' -and $_.testStatus -eq 'expected' -and $_.resultStatus -eq 'passed' -and $_.resultCount -eq 1 }).Count -eq 1
    $receipt = [ordered]@{
        phase = $Phase
        specCount = $specs.Count
        passedByFile = $counts
        playwrightStats = $report.stats
        expectedStatsExact = $expectedStats
        reportErrorsEmpty = $errorsEmpty
        oneAttemptPerSpec = $allPassed
        inventoryExact = $countsExact
        egressGuardVerifiedByPassingAutoFixtureAndSmoke = $smokePassed -and $allPassed
        egressEvidence = if ($smokePassed -and $allPassed) {
            'The passing smoke test verified blocked browser HTTP, redirect, and WebSocket probes with zero sibling-listener hits; it also asserted empty backend socket denials and synthetic-provider-only HTTPX hosts. The automatic fixture passed for every spec.'
        } else { 'Not proven because the smoke test and complete passing inventory are required.' }
        attempts = @($attempts)
    }
    $receiptPath = Join-Path $logs "$Phase-inventory-audit.json"
    [IO.File]::WriteAllText($receiptPath, ($receipt | ConvertTo-Json -Depth 12), $utf8)
    Write-Host "PLAYWRIGHT_INVENTORY $($receipt | ConvertTo-Json -Compress -Depth 8)"
    if (-not ($expectedStats -and $errorsEmpty -and $allPassed -and $countsExact -and $smokePassed)) {
        throw "$Phase must pass the exact 30-test Playwright inventory with one attempt per test."
    }
}

function Assert-PhaseSuccess([object]$Run, [object]$Audit = $null) {
    if ($Run.exitCode -ne 0) { throw "Phase $($Run.name) exited with code $($Run.exitCode)." }
    if ($null -ne $Audit -and -not $Audit.passed) {
        throw "Windows lifecycle audit failed for $($Run.name): $($Audit.issues -join '; ')"
    }
}

$nodeVersion = (& $node --version).Trim()
if ($LASTEXITCODE -ne 0 -or $nodeVersion -notmatch '^v22\.') { throw "Expected Node 22.x; found $nodeVersion." }
$pythonVersion = (& $python --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $pythonVersion -notmatch '^Python 3\.12\.') { throw "Expected Python 3.12; found $pythonVersion." }
$playwrightVersion = (Get-Content -LiteralPath (Join-Path $repository 'frontend\node_modules\@playwright\test\package.json') -Raw | ConvertFrom-Json).version
if ($playwrightVersion -ne '1.65.0') { throw "Expected locked Playwright 1.65.0; found $playwrightVersion." }
$browser = Get-Content -LiteralPath (Join-Path $repository 'frontend\node_modules\playwright-core\browsers.json') -Raw | ConvertFrom-Json
$chromium = @($browser.browsers | Where-Object { $_.name -eq 'chromium' })
if ($chromium.Count -ne 1) { throw 'The Playwright package must resolve exactly one Chromium revision.' }
Write-Host "E2E_RUNTIME node=$nodeVersion python=$pythonVersion playwright=$playwrightVersion chromium=$($chromium[0].browserVersion) revision=$($chromium[0].revision)"

Assert-ChromiumOwnershipSelfCheck
Write-Host 'CHROMIUM_IDENTITY_SELF_CHECK=passed'

$baseAncestry = Invoke-LoggedProcess -Name 'git-base-ancestry' -Executable 'git' -Arguments @('merge-base', '--is-ancestor', $DiffBase, 'HEAD')
Assert-PhaseSuccess $baseAncestry

foreach ($phase in @(
    [pscustomobject]@{ name = 'build'; executable = $node; args = @('frontend/e2e/build-production.mjs'); cwd = $repository },
    [pscustomobject]@{ name = 'node-checks'; executable = $node; args = @('--test', 'frontend/e2e/owned-root.node-check.mjs'); cwd = $repository }
)) {
    $run = Invoke-LoggedProcess -Name $phase.name -Executable $phase.executable -Arguments $phase.args -WorkingDirectory $phase.cwd
    if ($phase.name -eq 'node-checks') {
        $tests = [regex]::Match($run.stdout, '(?m)^# tests\s+(\d+)\s*$')
        $passed = [regex]::Match($run.stdout, '(?m)^# pass\s+(\d+)\s*$')
        $failed = [regex]::Match($run.stdout, '(?m)^# fail\s+(\d+)\s*$')
        if (-not $tests.Success -or $tests.Groups[1].Value -ne '7' -or -not $passed.Success -or $passed.Groups[1].Value -ne '7' -or -not $failed.Success -or $failed.Groups[1].Value -ne '0') {
            throw 'Node lifecycle checks must report exactly 7 passed and 0 failed.'
        }
    }
    Assert-PhaseSuccess $run
}

$preflightSource = "import setup from './frontend/e2e/global-setup.mjs'; const stop = await setup({ workers: 1 }); await stop();"
$preflight = Invoke-LoggedProcess -Name 'preflight' -Executable $node -Arguments @('--input-type=module', '-e', $preflightSource)
$preflightAudit = Write-HarnessAudit -Run $preflight -CheckChromium $false -ChromiumBaseline @()
Assert-PhaseSuccess $preflight $preflightAudit

$pythonRun = Invoke-LoggedProcess -Name 'python' -Executable $python -Arguments @('-m', 'pytest', '-ra')
Assert-PhaseSuccess $pythonRun

$frontend = Join-Path $repository 'frontend'
$frontendRun = Invoke-LoggedProcess -Name 'frontend' -Executable $node -Arguments @('node_modules/vitest/vitest.mjs', 'run') -WorkingDirectory $frontend
Assert-PhaseSuccess $frontendRun

for ($iteration = 1; $iteration -le 3; $iteration++) {
    $phaseName = "e2e-pass-$iteration"
    $reportPath = Join-Path $logs "$phaseName.report.json"
    $outputPath = Join-Path $traces $phaseName
    if ((Test-Path -LiteralPath $reportPath) -or (Test-Path -LiteralPath $outputPath)) { throw "Refusing to reuse $phaseName report or trace directory." }
    $arguments = @(
        $playwright, 'test', '--config=frontend/playwright.config.js',
        '--workers=1', '--retries=0', '--max-failures=1',
        "--output=$outputPath", '--reporter=list,json'
    )
    $passBaseline = @(Get-ChromiumInventory)
    $run = Invoke-LoggedProcess -Name $phaseName -Executable $node -Arguments $arguments -Environment @{ PLAYWRIGHT_JSON_OUTPUT_FILE = $reportPath }
    $audit = Write-HarnessAudit -Run $run -CheckChromium $true -ChromiumBaseline $passBaseline
    $inventoryFailure = $null
    try { Write-PlaywrightInventoryAudit -Phase $phaseName -ReportPath $reportPath }
    catch { $inventoryFailure = $_.Exception.Message }
    Assert-PhaseSuccess $run $audit
    if ($inventoryFailure) { throw $inventoryFailure }
}

$symlinkTest = Invoke-LoggedProcess -Name 'symlink-coverage' -Executable $python -Arguments @(
    '-m', 'pytest', '-q', '-rs', 'tests/test_e2e_harness_independent.py::test_owned_root_and_paths_reject_synthetic_symlinks_when_supported'
)
if ($symlinkTest.exitCode -ne 0) { throw 'The focused symlink coverage test failed.' }
$symlinkOutput = "$($symlinkTest.stdout)`n$($symlinkTest.stderr)"
$skip = [regex]::Match($symlinkOutput, '(?im)^SKIPPED\s+\[\d+\]\s+[^\r\n]*Directory symlinks are unavailable in this environment: ([^\r\n]+)')
$symlinkOutcome = if ($skip.Success) { 'skipped-unavailable' } elseif ($symlinkOutput -match '(?im)^1 passed(?:\s|$)') { 'executed-passed' } else { throw 'The symlink test outcome could not be identified; no coverage claim is allowed.' }
$symlinkReceipt = [ordered]@{ test = 'test_owned_root_and_paths_reject_synthetic_symlinks_when_supported'; outcome = $symlinkOutcome; skipReason = if ($skip.Success) { "Directory symlinks are unavailable in this environment: $($skip.Groups[1].Value)" } else { $null } }
[IO.File]::WriteAllText((Join-Path $logs 'symlink-coverage-audit.json'), ($symlinkReceipt | ConvertTo-Json -Depth 4), $utf8)
Write-Host "SYMLINK_COVERAGE $($symlinkReceipt | ConvertTo-Json -Compress)"

$privacyRun = Invoke-LoggedProcess -Name 'privacy-source-controls' -Executable $python -Arguments @(
    '-m', 'pytest', '-ra', 'tests/test_no_personal_data.py', 'tests/test_shoot_checks.py'
)
Assert-PhaseSuccess $privacyRun

$diffCheck = Invoke-LoggedProcess -Name 'diff-check' -Executable 'git' -Arguments @('diff', '--check', $DiffBase, 'HEAD')
Assert-PhaseSuccess $diffCheck

Write-Host "WINDOWS_E2E_VALIDATION_COMPLETE passes=3 testsPerPass=30 python=passed frontend=passed privacy=passed nodeLifecycle=7/7 symlink=$symlinkOutcome"
