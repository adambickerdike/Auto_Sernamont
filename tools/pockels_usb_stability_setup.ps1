# Run this once from an elevated PowerShell window, then reboot Windows.
# It disables the two Windows power-saving policies currently enabled on the
# USB hubs used by the Pockels setup. It does not disable or remove any device.

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$isAdministrator = $principal.IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdministrator) {
    throw "Open PowerShell with 'Run as administrator', then run this script again."
}

$usbSubgroup = "2a737441-1930-4402-8d77-b2bebba308a3"
$selectiveSuspend = "48e6b7a6-50f5-4782-a5d4-53bb8f07e226"
$activeSchemeText = (& powercfg.exe /GETACTIVESCHEME | Out-String)
$schemeMatch = [regex]::Match(
    $activeSchemeText,
    "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
if (-not $schemeMatch.Success) {
    throw "Could not determine the active Windows power scheme."
}
$activeScheme = $schemeMatch.Value

Write-Host "Disabling USB selective suspend for AC and battery power..."
& powercfg.exe /SETACVALUEINDEX $activeScheme $usbSubgroup $selectiveSuspend 0
if ($LASTEXITCODE -ne 0) {
    throw "powercfg failed while changing the AC selective-suspend setting."
}
& powercfg.exe /SETDCVALUEINDEX $activeScheme $usbSubgroup $selectiveSuspend 0
if ($LASTEXITCODE -ne 0) {
    throw "powercfg failed while changing the battery selective-suspend setting."
}
& powercfg.exe /SETACTIVE $activeScheme
if ($LASTEXITCODE -ne 0) {
    throw "powercfg failed while reactivating the updated power scheme."
}

# These are the two physical hub families in the current Pockels USB chain.
# Their USB 2 and USB 3 companion devices have separate power-policy entries.
$instrumentHubPattern = "VID_2109&PID_(2817|0817)|VID_35D6&PID_(2510|3510)"
$instrumentHubs = @(
    Get-CimInstance -Namespace root/wmi -ClassName MSPower_DeviceEnable |
        Where-Object { $_.InstanceName -match $instrumentHubPattern }
)
if (-not $instrumentHubs) {
    Write-Warning "No matching Pockels USB hub power-policy entries were found."
}
foreach ($hub in $instrumentHubs) {
    if ($hub.Enable) {
        Write-Host "Disabling Windows hub power-off permission: $($hub.InstanceName)"
        Set-CimInstance -InputObject $hub -Property @{ Enable = $false } | Out-Null
    }
}

Write-Host ""
Write-Host "USB power-policy changes applied. Reboot Windows before measuring."
Write-Host "This cannot repair a marginal hub, power brick, upstream cable, or nested-hub topology."
Write-Host "Scope/function generator should be moved to direct rear motherboard ports,"
Write-Host "with the camera and rotators on separate powered branches."
