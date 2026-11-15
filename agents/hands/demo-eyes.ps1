# Live demo of the hands' eyes: Kosika looks at Notepad's window, finds the
# "View" menu by its name, and presses it with its own pointer, asking you before each action.
# Notepad must be open. Answer y to each; hold Escape to stop it.
param([string]$Agent = "coder-2")
# The Kosika engine: KOSIKA_HOME, or the kosika folder beside kosika-agents.
$env:PYTHONPATH = if ($env:KOSIKA_HOME) { $env:KOSIKA_HOME } else { (Resolve-Path "$PSScriptRoot\..\..\..\kosika").Path }
$env:PYTHONIOENCODING = "utf-8"
$target = Join-Path $env:TEMP "kosika-hands-target.json"
[IO.File]::WriteAllText($target, ('{"t": {"agent": "' + $Agent + '", "app": "notepad", "name": "View"}}'))
Set-Location $PSScriptRoot
python -m kosika.cli run main.kos --workflow press --input $target --allow-shell --allow-folder . --approve --approved-by Aryan
