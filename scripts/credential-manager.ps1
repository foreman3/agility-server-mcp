# Fixed script, never assembled from user input. Requests and secrets arrive over stdin.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class AgilityCredentials {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct Credential {
        public uint Flags, Type;
        public string TargetName, Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public uint CredentialBlobSize;
        public IntPtr CredentialBlob;
        public uint Persist, AttributeCount;
        public IntPtr Attributes;
        public string TargetAlias, UserName;
    }
    [DllImport("advapi32.dll", EntryPoint="CredReadW", CharSet=CharSet.Unicode, SetLastError=true)]
    private static extern bool Read(string target, uint type, uint flags, out IntPtr credential);
    [DllImport("advapi32.dll", EntryPoint="CredWriteW", CharSet=CharSet.Unicode, SetLastError=true)]
    private static extern bool Write(ref Credential credential, uint flags);
    [DllImport("advapi32.dll", EntryPoint="CredDeleteW", CharSet=CharSet.Unicode, SetLastError=true)]
    private static extern bool Delete(string target, uint type, uint flags);
    [DllImport("advapi32.dll")] private static extern void CredFree(IntPtr credential);
    public static string[] Get(string target, bool includeSecret) {
        IntPtr pointer;
        if (!Read(target, 1, 0, out pointer)) {
            int code = Marshal.GetLastWin32Error();
            if (code == 1168) return null;
            throw new Win32Exception(code);
        }
        try {
            Credential c = (Credential)Marshal.PtrToStructure(pointer, typeof(Credential));
            if (c.CredentialBlobSize % 2 != 0) throw new InvalidOperationException("Credential must contain a UTF-16 password.");
            return new string[] { c.UserName, includeSecret ? Marshal.PtrToStringUni(c.CredentialBlob, (int)c.CredentialBlobSize / 2) : null };
        } finally { CredFree(pointer); }
    }
    public static void Set(string target, string user, string secret) {
        if (String.IsNullOrEmpty(secret) || secret.Length * 2 > 2560 || secret.IndexOf('\0') >= 0)
            throw new ArgumentException("Secret must contain 1 to 1280 UTF-16 characters, without NUL.");
        IntPtr blob = Marshal.StringToCoTaskMemUni(secret);
        try {
            Credential c = new Credential();
            c.Type = 1; c.TargetName = target; c.UserName = user;
            c.CredentialBlobSize = (uint)(secret.Length * 2); c.CredentialBlob = blob;
            c.Persist = 2; c.Comment = "Digital.ai Agility MCP";
            if (!Write(ref c, 0)) throw new Win32Exception(Marshal.GetLastWin32Error());
        } finally { Marshal.ZeroFreeCoTaskMemUnicode(blob); }
    }
    public static bool Remove(string target) {
        if (Delete(target, 1, 0)) return true;
        int code = Marshal.GetLastWin32Error();
        if (code == 1168) return false;
        throw new Win32Exception(code);
    }
}
'@
    if ([string]::IsNullOrEmpty($request.target) -or $request.target.Length -gt 256) { throw 'Invalid credential target.' }
    switch ($request.action) {
        'get' {
            $credential = [AgilityCredentials]::Get($request.target, $true)
            if ($null -eq $credential) { @{ found = $false } | ConvertTo-Json -Compress }
            else { @{ found = $true; username = $credential[0]; secret = $credential[1] } | ConvertTo-Json -Compress }
        }
        'status' {
            $credential = [AgilityCredentials]::Get($request.target, $false)
            @{ found = ($null -ne $credential) } | ConvertTo-Json -Compress
        }
        'set' {
            [AgilityCredentials]::Set($request.target, $request.username, $request.secret)
            @{ stored = $true } | ConvertTo-Json -Compress
        }
        'delete' { @{ deleted = [AgilityCredentials]::Remove($request.target) } | ConvertTo-Json -Compress }
        default { throw 'Unknown credential action.' }
    }
} catch {
    # Never echo input, compiler diagnostics, or exception text that could contain a secret.
    [Console]::Error.WriteLine('Windows Credential Manager operation failed. Check the Windows user session and PowerShell policy.')
    exit 1
}
