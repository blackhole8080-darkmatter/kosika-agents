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

param([switch]$Wait, [int]$Size = 4)

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$projects = Resolve-Path (Join-Path $here "..\..\..\..")
$daily = Join-Path $env:USERPROFILE "Aryan_Private\kosika-daily"
New-Item -ItemType Directory -Force $daily | Out-Null
$todo = Join-Path $daily "studio-todo.md"
# The list is copied once; after that its marks are the swarm's record.
if (-not (Test-Path $todo)) { Copy-Item (Join-Path $here "studio_todo.md") $todo }

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
