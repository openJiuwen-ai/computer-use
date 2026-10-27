param([Parameter(Mandatory = $true)][string]$Path)
$ErrorActionPreference = 'Stop'
$acl = [System.IO.File]::GetAccessControl($Path)
@{ user = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value; access = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]) | ForEach-Object {
            @{ sid = $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value; rights = [int]$_.FileSystemRights; inherited = $_.IsInherited; type = [string]$_.AccessControlType }
        })
} | ConvertTo-Json -Compress
