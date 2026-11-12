<#
Ostium's private package feed on the owner's Windows machine (ADR-0009 amendment 1, ADR-0010's
developer credentials; docs/runbooks/ostium-packages.md). Run through just:

    just ostium-feed save          asks for the feed token, keeps it in Windows Credential Manager
    just ostium-feed restore       a locked restore of the solution with it
    just ostium-feed restore -Pin  a version change: restore that writes the lock files
    just ostium-feed forget        removes the token from Credential Manager
    just ostium-feed local-sync    copies the pinned Ostium.* packages into the local folder feed
    just ostium-feed local-check   the local feed holds every one, and a locked restore works
                                   from it alone, without the private feed
    just ostium-feed local-remove -Package ID -Version V
                                   takes a version Ostium withdrew out of the local feed

The local folder feed (ADR-0009 amendment 1, WP3.5) is ~/.aetherspark/packages in WSL2, next to the
sealed store, so the backup job takes it into every snapshot; Windows reaches it as
\\wsl.localhost\<distribution>\... The local actions need no token.

The token is a classic GitHub token with only read:packages and an expiry. It lives in Credential
Manager (this user, this machine, never roaming) and, during a restore, in the environment of this
process and the one `dotnet restore` it starts (no build server stays behind). It is never printed,
written to a file or a NuGet config, or given to services or dev agents.
#>
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('save', 'restore', 'forget', 'local-sync', 'local-check', 'local-remove')]
    [string]$Action,
    [switch]$Pin,
    [string]$Package,
    [string]$Version
)
$ErrorActionPreference = 'Stop'
$Target = 'aetherspark/ostium-feed'
$Variable = 'NuGetPackageSourceCredentials_ostium'  # NuGet's name for the source "ostium"
$Root = Split-Path -Parent $PSScriptRoot

Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

public static class AetherSparkCredential
{
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct CREDENTIAL
    {
        public uint Flags;
        public uint Type;
        public string TargetName;
        public string Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public uint CredentialBlobSize;
        public IntPtr CredentialBlob;
        public uint Persist;
        public uint AttributeCount;
        public IntPtr Attributes;
        public string TargetAlias;
        public string UserName;
    }

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CredReadW(string target, uint type, uint flags, out IntPtr credential);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CredWriteW(ref CREDENTIAL credential, uint flags);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CredDeleteW(string target, uint type, uint flags);
    [DllImport("advapi32.dll")]
    private static extern void CredFree(IntPtr credential);

    private const uint Generic = 1;
    private const uint LocalMachine = 2;  // this user on this machine; not roaming

    public static string Read(string target)
    {
        IntPtr pointer;
        if (!CredReadW(target, Generic, 0, out pointer)) return null;
        try
        {
            var credential = (CREDENTIAL)Marshal.PtrToStructure(pointer, typeof(CREDENTIAL));
            return Marshal.PtrToStringUni(credential.CredentialBlob, (int)credential.CredentialBlobSize / 2);
        }
        finally { CredFree(pointer); }
    }

    public static void Write(string target, string user, string secret)
    {
        var bytes = Encoding.Unicode.GetBytes(secret);
        var blob = Marshal.AllocHGlobal(bytes.Length);
        try
        {
            Marshal.Copy(bytes, 0, blob, bytes.Length);
            var credential = new CREDENTIAL
            {
                Type = Generic, TargetName = target, UserName = user,
                CredentialBlob = blob, CredentialBlobSize = (uint)bytes.Length, Persist = LocalMachine,
            };
            if (!CredWriteW(ref credential, 0)) throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        finally
        {
            Marshal.Copy(new byte[bytes.Length], 0, blob, bytes.Length);
            Marshal.FreeHGlobal(blob);
            Array.Clear(bytes, 0, bytes.Length);
        }
    }

    public static bool Delete(string target) { return CredDeleteW(target, Generic, 0); }
}
'@

# The feed's owner, from nuget.config: GitHub Packages wants that account as the user name
$config = Get-Content -Raw (Join-Path $Root 'nuget.config')
if ($config -notmatch 'key="ostium" value="https://nuget\.pkg\.github\.com/([A-Za-z0-9-]+)/index\.json"') {
    throw 'nuget.config has no source "ostium" on nuget.pkg.github.com'
}
$Owner = $Matches[1]

# The local folder feed in WSL2, as Windows reaches it; made if missing
function Get-LocalFeed {
    $distro = if ($env:AETHERSPARK_WSL_DISTRO) { $env:AETHERSPARK_WSL_DISTRO } else { 'Ubuntu-24.04' }
    $linuxHome = (& wsl.exe -d $distro -- sh -c 'mkdir -p "$HOME/.aetherspark/packages" && chmod 0700 "$HOME/.aetherspark/packages" && echo "$HOME"').Trim()
    if (-not $linuxHome.StartsWith('/')) { throw "WSL distribution $distro did not answer" }
    return "\\wsl.localhost\$distro" + ($linuxHome -replace '/', '\') + '\.aetherspark\packages'
}
function Invoke-LocalFeed([string[]]$Arguments) {
    & python -I (Join-Path $Root 'deploy\packages\local_feed.py') @Arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

switch ($Action) {
    'local-sync' {
        Invoke-LocalFeed @('sync', '--feed', (Get-LocalFeed))
    }
    'local-check' {
        $feed = Get-LocalFeed
        Invoke-LocalFeed @('check', '--feed', $feed)
        Invoke-LocalFeed @('prove', '--feed', $feed)
    }
    'local-remove' {
        if (-not $Package -or -not $Version) { throw 'local-remove needs -Package and -Version' }
        Invoke-LocalFeed @('remove', '--feed', (Get-LocalFeed), $Package, $Version)
    }
    'save' {
        $secure = Read-Host 'Ostium feed token (classic, read:packages only, with an expiry)' -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try {
            $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
            if ($token -notmatch '^ghp_[A-Za-z0-9]{36}$') {
                throw 'not a classic GitHub token (ghp_...): GitHub Packages takes no fine-grained token'
            }
            [AetherSparkCredential]::Write($Target, $Owner, $token)
        }
        finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
            $token = $null
        }
        Write-Output "ostium-feed: saved in Windows Credential Manager as $Target"
    }
    'forget' {
        if ([AetherSparkCredential]::Delete($Target)) { Write-Output "ostium-feed: $Target removed" }
        else { Write-Output "ostium-feed: no $Target saved" }
    }
    'restore' {
        $token = [AetherSparkCredential]::Read($Target)
        if (-not $token) { throw "no token saved: run 'just ostium-feed save' first" }
        $mode = if ($Pin) { '--force-evaluate' } else { '--locked-mode' }
        Set-Item "Env:$Variable" "Username=$Owner;Password=$token"
        $token = $null
        try {
            & dotnet restore (Join-Path $Root 'AetherSpark.slnx') $mode --disable-build-servers
            $code = $LASTEXITCODE
        }
        finally {
            Remove-Item "Env:$Variable" -ErrorAction SilentlyContinue
        }
        if ($code -ne 0) { exit $code }
        Write-Output "ostium-feed: restore done ($mode)"
    }
}
