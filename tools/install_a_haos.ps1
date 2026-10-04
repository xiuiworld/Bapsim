#Requires -RunAsAdministrator
#Requires -Modules Hyper-V
[CmdletBinding()]
param(
    [string]$AdapterDescription = 'Realtek USB 2.5GbE Family Controller',
    [ValidateRange(2,8)][int]$MemoryGB = 2,
    [switch]$ResumeExisting
)

$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskDir = Join-Path $taskRoot 'local-data\haos'
$taskDisk = Join-Path $taskDir '18.3\haos_ova-18.3.vhdx'
$taskVmName = 'Bapsim-HAOS'
$taskSwitchName = 'Bapsim-LAN'
if (-not (Test-Path -LiteralPath $taskDisk)) { throw "HA OS disk missing: $taskDisk" }
Start-Transcript -Path (Join-Path $taskDir 'install.log') -Append
try {
    $taskExistingVm = Get-VM -Name $taskVmName -ErrorAction SilentlyContinue
    if ($taskExistingVm) {
        if ($ResumeExisting) {
            $taskAttachedDisks = @(Get-VMHardDiskDrive -VMName $taskVmName)
            if ($taskAttachedDisks.Count -ne 1) {
                throw 'Expected exactly one VM disk. No changes made.'
            }
            # Automatic checkpoints attach an AVHDX child rather than the base VHDX.
            # Validate the actual parent chain, not the child filename.
            $taskExpectedPath = [IO.Path]::GetFullPath($taskDisk)
            $taskCurrentPath = $taskAttachedDisks[0].Path
            $taskSeenPaths = @{}
            $taskDiskMatches = $false
            for ($taskDepth = 0; $taskDepth -lt 64; $taskDepth++) {
                if ([string]::IsNullOrWhiteSpace($taskCurrentPath)) { break }
                $taskCurrentPath = [IO.Path]::GetFullPath($taskCurrentPath)
                if ($taskSeenPaths.ContainsKey($taskCurrentPath)) { throw 'Cycle in disk parent chain.' }
                $taskSeenPaths[$taskCurrentPath] = $true
                Write-Host "Checking VM disk: $taskCurrentPath"
                $taskVhd = Get-VHD -Path $taskCurrentPath
                if ($taskCurrentPath -eq $taskExpectedPath) {
                    $taskDiskMatches = $true
                    break
                }
                if ($taskVhd.VhdType -ne 'Differencing' -or [string]::IsNullOrWhiteSpace($taskVhd.ParentPath)) { break }
                $taskCurrentPath = $taskVhd.ParentPath
            }
            if (-not $taskDiskMatches) {
                throw "VM disk parent chain does not reach expected disk: $taskExpectedPath. No changes made."
            }
            if ($taskExistingVm.State -eq 'Off') {
                Set-VMMemory -VMName $taskVmName -DynamicMemoryEnabled $false -StartupBytes ($MemoryGB * 1GB)
                Start-VM -Name $taskVmName
                Write-Host "Existing HA OS VM started with $MemoryGB GiB RAM."
            } elseif ($taskExistingVm.State -ne 'Running') {
                throw 'VM must be Off or Running. No automatic stop or reset performed.'
            }
            Get-VM -Name $taskVmName | Format-Table Name, State
            Get-VMNetworkAdapter -VMName $taskVmName | Format-List SwitchName, IPAddresses
            return
        }
        Write-Host 'Bapsim-HAOS already exists. No VM settings were changed.'
        $taskExistingVm | Format-Table Name, State
        Get-VMNetworkAdapter -VMName $taskVmName | Format-List SwitchName, IPAddresses
        return
    }
    $taskAdapter = Get-NetAdapter -InterfaceDescription $AdapterDescription
    if ($taskAdapter.Status -ne 'Up') { throw 'Selected physical adapter is not connected.' }
    $taskSwitch = Get-VMSwitch | Where-Object {
        $_.SwitchType -eq 'External' -and
        $_.NetAdapterInterfaceDescription -eq $taskAdapter.InterfaceDescription
    } | Select-Object -First 1
    if (-not $taskSwitch) {
        if (Get-VMSwitch -Name $taskSwitchName -ErrorAction SilentlyContinue) {
            throw 'Bapsim-LAN exists with a different configuration. Inspect it before proceeding.'
        }
        Write-Host 'Creating an external switch. The host network may disconnect briefly.'
        $taskSwitch = New-VMSwitch -Name $taskSwitchName -NetAdapterName $taskAdapter.Name -AllowManagementOS $true
    }
    New-VM -Name $taskVmName -Generation 2 -MemoryStartupBytes ($MemoryGB * 1GB) -VHDPath $taskDisk -SwitchName $taskSwitch.Name -Path $taskDir | Out-Null
    Set-VMProcessor -VMName $taskVmName -Count 2
    Set-VMMemory -VMName $taskVmName -DynamicMemoryEnabled $false
    Set-VMFirmware -VMName $taskVmName -EnableSecureBoot Off
    $taskBootDisk = Get-VMHardDiskDrive -VMName $taskVmName | Select-Object -First 1
    Set-VMFirmware -VMName $taskVmName -FirstBootDevice $taskBootDisk
    Set-VM -Name $taskVmName -AutomaticStartAction StartIfRunning -AutomaticStopAction ShutDown
    Start-VM -Name $taskVmName
    Write-Host 'HA OS started. Initial setup can take several minutes.'
    Write-Host 'Open http://homeassistant.local:8123 or use the VM IPv4 address on port 8123.'
    Get-VM -Name $taskVmName | Format-Table Name, State
    Get-VMNetworkAdapter -VMName $taskVmName | Format-List SwitchName, IPAddresses
} finally {
    Stop-Transcript
}
