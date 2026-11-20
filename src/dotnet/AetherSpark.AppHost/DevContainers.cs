using System.Diagnostics;

namespace AetherSpark.AppHost;

/// <summary>
/// What an earlier `aspire run` left behind when it was killed instead of stopped (a closed terminal
/// window: Aspire then removes nothing), removed at the next start. Never another project's on the
/// same Docker:
/// <list type="bullet">
/// <item>containers: only those with this AppHost's label;</item>
/// <item>networks: only Aspire's session network for this AppHost (its name ends in
/// <see cref="SessionNetworkSuffix"/>, as measured on 2026-11-20), not persistent, with no container
/// attached, and whose creating orchestrator process is no longer running.</item>
/// </list>
/// </summary>
internal static class DevContainers
{
    public const string Label = "aetherspark.dev=aspire-run";

    private const string SessionNetworkPrefix = "aspire-session-network-";
    private const string SessionNetworkSuffix = "-AetherSpark";
    private const string CreatorLabel = "com.microsoft.developer.usvc-dev.creatorProcessId";
    private const string PersistentLabel = "com.microsoft.developer.usvc-dev.persistent";

    public static void RemoveLeftovers()
    {
        var ids = Lines(Docker("ps", "--all", "--quiet", "--filter", $"label={Label}"));
        if (ids.Length > 0)
        {
            // --volumes: with them any anonymous volume (the database lives in memory, so none is expected)
            Docker(["rm", "--force", "--volumes", .. ids]);
        }

        var networks = Lines(Docker("network", "ls", "--filter", $"label={PersistentLabel}=false", "--format", "{{.Name}}"))
            .Where(n => n.StartsWith(SessionNetworkPrefix, StringComparison.Ordinal)
                        && n.EndsWith(SessionNetworkSuffix, StringComparison.Ordinal));
        foreach (var network in networks)
        {
            var facts = Docker("network", "inspect", network, "--format", $"{{{{len .Containers}}}} {{{{index .Labels \"{CreatorLabel}\"}}}}")
                .Trim().Split(' ');
            if (facts is [var attached, var creator] && attached == "0" && !IsRunning(creator))
            {
                Docker("network", "rm", network);
            }
        }
    }

    // An unreadable or reused process number counts as running: the network is then left alone
    private static bool IsRunning(string processId)
    {
        if (!int.TryParse(processId, System.Globalization.NumberStyles.None, System.Globalization.CultureInfo.InvariantCulture, out var id))
        {
            return true;
        }

        try
        {
            using var process = Process.GetProcessById(id);
            return !process.HasExited;
        }
        catch (ArgumentException)
        {
            return false; // no process with that number
        }
    }

    private static string[] Lines(string output) =>
        output.Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

    private static string Docker(params string[] arguments)
    {
        var start = new ProcessStartInfo("docker")
        {
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        foreach (var argument in arguments)
        {
            start.ArgumentList.Add(argument);
        }

        using var process = Process.Start(start) ?? throw new InvalidOperationException("docker did not start");
        var output = process.StandardOutput.ReadToEndAsync();
        var errors = process.StandardError.ReadToEndAsync();
        process.WaitForExit();
        return process.ExitCode == 0
            ? output.GetAwaiter().GetResult()
            : throw new InvalidOperationException($"docker {arguments[0]} failed: {errors.GetAwaiter().GetResult().Trim()}");
    }
}
