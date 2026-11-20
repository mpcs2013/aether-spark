using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Npgsql;

namespace AetherSpark.Ingester;

/// <summary>
/// The ingester has no listener, so its health is a file: touched after each successful query to the
/// data Postgres over its TLS login. The container's health check reads the file's age.
/// </summary>
public sealed partial class Heartbeat(NpgsqlDataSource dataSource, IngesterSettings settings, ILogger<Heartbeat> logger)
    : BackgroundService
{
    public static readonly TimeSpan Interval = TimeSpan.FromSeconds(15);

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                await using var command = dataSource.CreateCommand("SELECT 1");
                await command.ExecuteScalarAsync(stoppingToken).ConfigureAwait(false);
                await File.WriteAllTextAsync(settings.HeartbeatFile, string.Empty, stoppingToken).ConfigureAwait(false);
            }
            catch (NpgsqlException e)
            {
                LogFailed(logger, e.Message);
            }

            await Task.Delay(Interval, stoppingToken).ConfigureAwait(false);
        }
    }

    [LoggerMessage(Level = LogLevel.Warning, Message = "database check failed: {Reason}")]
    private static partial void LogFailed(ILogger logger, string reason);
}
