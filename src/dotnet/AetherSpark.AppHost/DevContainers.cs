using System.Diagnostics;

namespace AetherSpark.AppHost;

/// <summary>
/// `aspire run`'s containers carry one label, so a start can remove what an earlier run left behind
/// when it was killed instead of stopped (a closed terminal window: Aspire then removes nothing).
/// Only containers with this label are touched, never another project's on the same Docker.
/// </summary>
internal static class DevContainers
{
    public const string Label = "aetherspark.dev=aspire-run";

    public static void RemoveLeftovers()
    {
        var found = Docker("ps", "--all", "--quiet", "--filter", $"label={Label}");
        var ids = found.Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        if (ids.Length > 0)
        {
            // --volumes: with them any anonymous volume (the database lives in memory, so none is expected)
            Docker(["rm", "--force", "--volumes", .. ids]);
        }
    }

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
