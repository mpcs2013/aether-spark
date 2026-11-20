using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Wolverine;

namespace AetherSpark.Ingester.Walk;

/// <summary>
/// Starts a walk at every start and then each day at <see cref="IngesterSettings.DailyWalkUtc"/>
/// (owner's decision of 2026-11-19). The walk itself is a durable message in `ingest_ops`, so a walk
/// cut short by a stop runs again at the next start; the timer keeps no state of its own, so restarts
/// never pile up scheduled walks.
/// </summary>
public sealed class WalkTimer(IServiceScopeFactory scopes, IngesterSettings settings, TimeProvider time) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        await SendAsync("start", stoppingToken).ConfigureAwait(false);
        while (!stoppingToken.IsCancellationRequested)
        {
            var now = time.GetUtcNow();
            await Task.Delay(Until(now, settings.DailyWalkUtc), time, stoppingToken).ConfigureAwait(false);
            await SendAsync("daily", stoppingToken).ConfigureAwait(false);
        }
    }

    /// <summary>The time from <paramref name="now"/> to the next <paramref name="at"/> (UTC), never zero.</summary>
    public static TimeSpan Until(DateTimeOffset now, TimeOnly at)
    {
        var utc = now.ToUniversalTime();
        var next = new DateTimeOffset(DateOnly.FromDateTime(utc.UtcDateTime).ToDateTime(at), TimeSpan.Zero);
        return (next > utc ? next : next.AddDays(1)) - utc;
    }

    private async Task SendAsync(string trigger, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        await using var scope = scopes.CreateAsyncScope();
        var bus = scope.ServiceProvider.GetRequiredService<IMessageBus>();
        await bus.PublishAsync(new WalkUniverse(trigger)).ConfigureAwait(false);
    }
}
