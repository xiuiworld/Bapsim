param([Parameter(Mandatory=$true)][string]$CommandText)
$ErrorActionPreference = 'Stop'
$taskToolchain = 'C:\ncs\toolchains\4f5b6ad6dd'
$taskConfig = Get-Content -LiteralPath "$taskToolchain\environment.json" -Raw | ConvertFrom-Json
foreach ($taskEntry in $taskConfig.env_vars) {
    if ($taskEntry.type -eq 'relative_paths') {
        $taskValue = ($taskEntry.values | ForEach-Object { Join-Path $taskToolchain $_ }) -join ';'
        if ($taskEntry.existing_value_treatment -eq 'prepend_to') {
            $taskValue += ';' + [Environment]::GetEnvironmentVariable($taskEntry.key, 'Process')
        }
    } else { $taskValue = $taskEntry.value }
    [Environment]::SetEnvironmentVariable($taskEntry.key, $taskValue, 'Process')
}
Set-Location 'C:\ncs\v3.4.1'
Invoke-Expression $CommandText
exit $LASTEXITCODE
