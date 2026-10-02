$ErrorActionPreference = 'Stop'

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class RecruitOpsUserObjects {
    [DllImport("user32.dll", SetLastError = true)]
    public static extern IntPtr GetProcessWindowStation();

    [DllImport("user32.dll", SetLastError = true)]
    public static extern IntPtr GetThreadDesktop(uint threadId);

    [DllImport("kernel32.dll")]
    public static extern uint GetCurrentThreadId();

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool GetUserObjectSecurity(IntPtr handle, ref uint information,
        byte[] descriptor, uint length, out uint needed);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool SetUserObjectSecurity(IntPtr handle, ref uint information,
        byte[] descriptor);
}
'@

function Grant-UserObjectAccess([IntPtr]$Handle, [int]$AccessMask) {
    if ($Handle -eq [IntPtr]::Zero) { throw 'Windows user object unavailable' }
    [uint32]$information = 4 # DACL_SECURITY_INFORMATION
    [uint32]$needed = 0
    [void][RecruitOpsUserObjects]::GetUserObjectSecurity($Handle, [ref]$information, $null, 0, [ref]$needed)
    if ($needed -eq 0) { throw 'Windows user object security descriptor unavailable' }
    $buffer = [byte[]]::new($needed)
    if (-not [RecruitOpsUserObjects]::GetUserObjectSecurity($Handle, [ref]$information, $buffer, $needed, [ref]$needed)) {
        throw "GetUserObjectSecurity failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
    }
    $descriptor = [System.Security.AccessControl.RawSecurityDescriptor]::new($buffer, 0)
    if ($null -eq $descriptor.DiscretionaryAcl) { return }
    $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $ace = [System.Security.AccessControl.CommonAce]::new(
        [System.Security.AccessControl.AceFlags]::None,
        [System.Security.AccessControl.AceQualifier]::AccessAllowed,
        $AccessMask, $user, $false, $null)
    $descriptor.DiscretionaryAcl.InsertAce($descriptor.DiscretionaryAcl.Count, $ace)
    $updated = [byte[]]::new($descriptor.BinaryLength)
    $descriptor.GetBinaryForm($updated, 0)
    if (-not [RecruitOpsUserObjects]::SetUserObjectSecurity($Handle, [ref]$information, $updated)) {
        throw "SetUserObjectSecurity failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
    }
}

Grant-UserObjectAccess ([RecruitOpsUserObjects]::GetProcessWindowStation()) 0x000F037F
Grant-UserObjectAccess ([RecruitOpsUserObjects]::GetThreadDesktop([RecruitOpsUserObjects]::GetCurrentThreadId())) 0x000F01FF
Write-Output 'Granted current user access to CI window station and desktop.'
