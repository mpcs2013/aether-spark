using AetherSpark.Ingester.Sec;
using AetherSpark.Ingester.Universe;
using Microsoft.Extensions.Logging;

namespace AetherSpark.Ingester.Walk;

/// <summary>Walk the universe: one durable FetchCompany message per company.</summary>
/// <param name="Trigger">`start` or `daily`, for the log.</param>
public sealed record WalkUniverse(string Trigger);

/// <summary>Fetch one company's new filings; run one at a time (Messaging).</summary>
public sealed record FetchCompany(long Cik);

/// <summary>Handles a walk; a Wolverine handler, found by its name.</summary>
public static partial class WalkUniverseHandler
{
    public static IEnumerable<FetchCompany> Handle(WalkUniverse walk, IngesterSettings settings, SecStop stop,
        ILogger<WalkUniverse> logger)
    {
        ArgumentNullException.ThrowIfNull(walk);
        ArgumentNullException.ThrowIfNull(settings);
        ArgumentNullException.ThrowIfNull(stop);
        var universe = UniverseFile.Read(settings.UniverseFile);
        stop.Reset();
        LogWalk(logger, walk.Trigger, universe.Count);
        return universe.Select(entry => new FetchCompany(entry.Cik)).ToList();
    }

    [LoggerMessage(Level = LogLevel.Information, Message = "universe walk ({Trigger}): {Count} companies")]
    private static partial void LogWalk(ILogger logger, string trigger, int count);
}

/// <summary>Handles one company; a Wolverine handler, found by its name.</summary>
public static class FetchCompanyHandler
{
    public static Task HandleAsync(FetchCompany message, CompanyFetcher fetcher, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(message);
        ArgumentNullException.ThrowIfNull(fetcher);
        return fetcher.FetchAsync(message.Cik, cancellationToken);
    }
}
