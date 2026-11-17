# The coder swarm's Studio job: four Kosika coders at once, each taking one
# request of studio_todo.md (a new icon designed in Kosika, a modern
# structure, the components they recommend, design tokens), on a copy of
# Kosika Studio as its branch has it on GitHub. Each coder shows on the screen
# with its own pointer and colour (agents/hands/overlay.py) while it works.
#
# Nothing reaches Kosika by itself: each run leaves change.patch and
# report.md in kosika-daily/DATE-N, for Studio's Coders page, where a change
# is kept only when you press Keep. It runs through the model router on port
# 8791 (free models and Kosika's own), started if it is not up.
#
#   .\studio_job.ps1                 # starts in the background and returns
#   .\studio_job.ps1 -Wait           # stays until the swarm is done

param([switch]$Wait, [int]$Size = 4, [string]$List = "studio_todo.md", [string]$Name = "studio-todo.md", [switch]$Next)

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$projects = Resolve-Path (Join-Path $here "..\..\..\..")
$daily = Join-Path $env:USERPROFILE "Aryan_Private\kosika-daily"
New-Item -ItemType Directory -Force $daily | Out-Null
$todo = Join-Path $daily $Name
# -Next: the swarm writes its own list. studio_plan.kos turns the components
# its coders ranked (the newest STUDIO_COMPONENTS.md that passed its checks)
# into requests, skipping ones already asked for.
if ($Next) {
  $todo = Join-Path $daily "studio-todo-next.md"
  $runs = Get-ChildItem $daily -Directory | Where-Object { Test-Path (Join-Path $_.FullName "kosika\STUDIO_COMPONENTS.md") } |
    Where-Object { (Get-Content (Join-Path $_.FullName "run.json") -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json).passed } |
    Sort-Object LastWriteTime
  if (-not $runs) { "No STUDIO_COMPONENTS.md has passed its checks yet; nothing to plan."; return }
  # Only what passed or is being worked on counts as done; a failed request is planned again.
  $asked = (Get-ChildItem $daily -Filter "studio-todo*.md" | Get-Content | Where-Object { $_ -match "^- \[(x|~)\] " }) -join "`n"
  $done = [regex]::Matches($asked, "app_assets/studio_([a-z0-9_]+)\.kos") | ForEach-Object { $_.Groups[1].Value.Replace("_", "-") }
  $plan = @{ p = @{ components = (Get-Content (Join-Path $runs[-1].FullName "kosika\STUDIO_COMPONENTS.md") -Raw); most = $Size; done = @($done) } }
  $ask = Join-Path $env:TEMP "studio-plan.json"
  $plan | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 $ask
  $env:PYTHONPATH = Join-Path $projects "kosika-studio-app"
  $said = & python -m kosika.cli run (Join-Path $here "studio_plan.kos") --workflow plan --input $ask 2>&1 | Where-Object { $_ -match "^\{" } | Select-Object -Last 1
  $lines = python -c "import ast,sys; print(chr(10).join(ast.literal_eval(sys.argv[1])['lines']))" $said
  if (-not $lines) { "The plan gave no new requests."; return }
  Add-Content -Encoding utf8 $todo $lines
  "Planned from $($runs[-1].Name):"; $lines | ForEach-Object { "  " + $_.Substring(0, [Math]::Min(110, $_.Length)) }
}
# The list is copied once; after that its marks are the swarm's record.
if (-not $Next -and -not (Test-Path $todo)) { Copy-Item (Join-Path $here $List) $todo }

$env:PYTHONIOENCODING = "utf-8"
$env:KOSIKA_ROUTER_PORT = "8791"
# The engine and the app come from the fresh Studio checkout, and each coder
# gets the app's branch as it is on GitHub, desktop app included.
$env:KOSIKA_HOME = Join-Path $projects "kosika-studio-app"
$env:KOSIKA_SOURCE_BRANCH = "claude/project-thread-lbxduo"
$env:KOSIKA_COPIED = "kosika,native,desktop,KOSIKA.md"

$log = Join-Path $daily "studio-job.log"
$python = (Get-Command python).Source
$run = Start-Process -FilePath $python -ArgumentList @((Join-Path $here "daily.py"), "--todo", $todo, "--swarm", $Size) `
  -WorkingDirectory $daily -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError "$log.err" -PassThru
"The Studio swarm is working (process $($run.Id)). Its log: $log"
if ($Wait) { $run | Wait-Process; Get-Content $log -Tail 20 }
