using AetherSpark.Ingester.Universe;
using Microsoft.Extensions.Logging;

namespace AetherSpark.Ingester.Walk;

/// <summary>Walk the universe: for each company, look for filings not yet stored (PR 3b2 fetches them).</summary>
/// <param name="Trigger">`start` or `daily`, for the log.</param>
public sealed record WalkUniverse(string Trigger);

/// <summary>Handles a walk; a Wolverine handler, found by its name.</summary>
public static partial class WalkUniverseHandler
{
    public static void Handle(WalkUniverse walk, IngesterSettings settings, ILogger<WalkUniverse> logger)
    {
        ArgumentNullException.ThrowIfNull(walk);
        ArgumentNullException.ThrowIfNull(settings);
        var universe = UniverseFile.Read(settings.UniverseFile);
        LogWalk(logger, walk.Trigger, universe.Count);
    }

    [LoggerMessage(Level = LogLevel.Information,
        Message = "universe walk ({Trigger}): {Count} companies; fetching comes with PR 3b2")]
    private static partial void LogWalk(ILogger logger, string trigger, int count);
}
