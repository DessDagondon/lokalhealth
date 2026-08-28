$requirementsPath = Join-Path $PSScriptRoot 'requirements.txt'
$pythonPath = Join-Path $PSScriptRoot 'venv\Scripts\python.exe'
$freezeOutput = & $pythonPath -m pip freeze | Out-String
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($requirementsPath, $freezeOutput.TrimEnd() + [Environment]::NewLine, $utf8NoBom)
Write-Host "Updated $requirementsPath as UTF-8."
