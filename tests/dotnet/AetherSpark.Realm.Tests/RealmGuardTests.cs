using System.Diagnostics;
using Ostium.Keycloak.Realm;

namespace AetherSpark.Realm.Tests;

/// <summary>
/// The realm guard: only the stack's Compose file imports the realm file into Keycloak, only
/// `just keycloak apply` and these tests read it, and prose may name it. Any other file that names
/// the realm file or Keycloak's import folder fails CI, so a second way into the realm is seen.
/// </summary>
public sealed class RealmGuardTests
{
    private const string TestDirectory = "tests/dotnet/AetherSpark.Realm.Tests/";
    private const string CaseTable = TestDirectory + "realm-guard-cases.json";

    private static readonly RealmGuard Guard = new(new RealmGuardOptions
    {
        RealmFileName = Realm.FileName,
        Exemptions =
        [
            // The stack: the one import (a single-file mount) and the environment it fills in
            GuardExemption.ExactPath("deploy/compose/stack/compose.yaml"),
            // `just keycloak apply`, which brings a running realm to the file
            GuardExemption.ExactPath("deploy/keycloak/admin.py"),
            // These tests: the profile, and the case table below
            GuardExemption.ExactPath(TestDirectory + "Realm.cs"),
            GuardExemption.ExactPath(CaseTable),
            // Docs and runbooks may name it; an agent's instructions may not
            GuardExemption.MarkdownOutsideAgentArea(),
        ],
    });

    [Fact]
    public void NoTrackedFileNamesTheRealmFileOrTheImportFolderOutsideTheExemptions()
    {
        var result = Guard.Scan(Realm.RepositoryRoot, TrackedFiles());
        Assert.True(result.IsClean, result.ToString());
    }

    [Fact]
    public void TheGuardDecidesEveryCaseAndEachExemptionIsNeeded()
    {
        var cases = RealmGuardCase.LoadTable(Path.Combine(Realm.RepositoryRoot, CaseTable));
        var report = Guard.Evaluate(cases);
        Assert.True(report.Passed, report.ToString());
    }

    /// <summary>
    /// Tracked files and new ones not ignored: never git-ignored local files such as secrets.
    /// </summary>
    private static List<string> TrackedFiles()
    {
        var start = new ProcessStartInfo("git")
        {
            WorkingDirectory = Realm.RepositoryRoot,
            RedirectStandardOutput = true,
            UseShellExecute = false,
        };
        foreach (var argument in new[] { "ls-files", "-z", "--cached", "--others", "--exclude-standard" })
        {
            start.ArgumentList.Add(argument);
        }

        using var git = Process.Start(start) ?? throw new InvalidOperationException("git did not start");
        var output = git.StandardOutput.ReadToEnd();
        git.WaitForExit();
        Assert.Equal(0, git.ExitCode);
        // A deleted file is still listed until the deletion is staged: the scan reports it as gone
        return output.Split('\0', StringSplitOptions.RemoveEmptyEntries).ToList();
    }
}
