using System.Diagnostics;
using Microsoft.Extensions.Diagnostics.HealthChecks;

namespace AetherSpark.AppHost;

/// <summary>
/// The development database is ready once its start script has put the roles, the database and the
/// migrations in place and touched /tmp/ready, as the stack's health check reads it (compose.yaml).
/// A listening port alone is too early: the ingester's start checks the migrations.
/// </summary>
internal sealed class ReadyMarkerCheck(string containerName) : IHealthCheck
{
    public async Task<HealthCheckResult> CheckHealthAsync(HealthCheckContext context, CancellationToken cancellationToken = default)
    {
        var start = new ProcessStartInfo("docker")
        {
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        foreach (var argument in new[] { "exec", containerName, "test", "-f", "/tmp/ready" })
        {
            start.ArgumentList.Add(argument);
        }

        try
        {
            using var process = Process.Start(start) ?? throw new InvalidOperationException("docker did not start");
            await process.WaitForExitAsync(cancellationToken).ConfigureAwait(false);
            return process.ExitCode == 0
                ? HealthCheckResult.Healthy()
                : HealthCheckResult.Unhealthy("roles, database or migrations not in place yet");
        }
        catch (System.ComponentModel.Win32Exception e)
        {
            return HealthCheckResult.Unhealthy("the docker command is not available", e);
        }
    }
}
