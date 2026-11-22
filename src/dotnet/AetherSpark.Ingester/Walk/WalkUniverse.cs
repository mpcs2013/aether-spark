using AetherSpark.Ingester.Sec;
using AetherSpark.Ingester.Universe;
using Microsoft.Extensions.Logging;

namespace AetherSpark.Ingester.Walk;

/// <summary>Walk the universe: one durable FetchCompany message per company.</summary>
/// <param name="Trigger">`start` or `daily`, for the log.</param>
public sealed record WalkUniverse(string Trigger);

/// <summary>
/// Fetch one company's new filings; run one at a time (Messaging). <paramref name="Walk"/>,
/// <paramref name="Trigger"/> and <paramref name="Companies"/> name the walk it belongs to, for its
/// end line; a message stored before they existed has none and counts toward no walk.
/// </summary>
public sealed record FetchCompany(long Cik, Guid? Walk = null, string? Trigger = null, int Companies = 0);

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
        var id = Guid.NewGuid();
        return universe.Select(entry => new FetchCompany(entry.Cik, id, walk.Trigger, universe.Count)).ToList();
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
        return fetcher.FetchForWalkAsync(message, cancellationToken);
    }
}

/// <summary>
/// Adds up a walk's companies and logs its end after the last one: how long it took and what it
/// did. Kept in memory: a walk whose companies a restart split between two runs gets no end line
/// (the next start walks again anyway); a company cancelled by a stop counts toward nothing.
/// </summary>
public sealed partial class WalkProgress(TimeProvider time, ILogger<WalkProgress> logger)
{
    private readonly Lock gate = new();
    private readonly Dictionary<Guid, Tally> walks = [];

    private sealed class Tally(DateTimeOffset firstStarted)
    {
        public DateTimeOffset FirstStarted { get; } = firstStarted;

        public int Done { get; set; }

        public int HandedOff { get; set; }

        public int Known { get; set; }

        public int Shared { get; set; }

        public int Refused { get; set; }

        public int CompaniesRefused { get; set; }

        public int Stopped { get; set; }
    }

    /// <summary>The company's first request is near: the walk's clock starts with its first company.</summary>
    public void Starting(FetchCompany message)
    {
        ArgumentNullException.ThrowIfNull(message);
        if (message.Walk is not { } id || message.Companies <= 0)
        {
            return;
        }

        lock (gate)
        {
            walks.TryAdd(id, new Tally(time.GetUtcNow()));
        }
    }

    public void Finished(FetchCompany message, CompanyResult result)
    {
        ArgumentNullException.ThrowIfNull(message);
        ArgumentNullException.ThrowIfNull(result);
        if (message.Walk is not { } id || message.Companies <= 0)
        {
            return;
        }

        Tally tally;
        lock (gate)
        {
            if (!walks.TryGetValue(id, out tally!))
            {
                tally = new Tally(time.GetUtcNow());
                walks[id] = tally;
            }

            tally.Done++;
            tally.HandedOff += result.HandedOff;
            tally.Known += result.Known;
            tally.Shared += result.Shared;
            tally.Refused += result.Refused;
            tally.CompaniesRefused += result.CompanyRefused ? 1 : 0;
            tally.Stopped += result.Stopped ? 1 : 0;
            if (tally.Done < message.Companies)
            {
                return;
            }

            walks.Remove(id);
        }

        var took = Took(time.GetUtcNow() - tally.FirstStarted);
        LogEnd(logger, message.Trigger ?? "?", message.Companies, took,
            tally.HandedOff, tally.Known, tally.Shared, tally.Refused, tally.CompaniesRefused, tally.Stopped);
    }

    /// <summary>"48 s", "3 min 48 s", "5 h 07 min".</summary>
    public static string Took(TimeSpan span)
    {
        var s = (long)Math.Max(0, Math.Round(span.TotalSeconds));
        return s switch
        {
            < 60 => $"{s} s",
            < 3600 => $"{s / 60} min {s % 60:00} s",
            _ => $"{s / 3600} h {s % 3600 / 60:00} min",
        };
    }

    [LoggerMessage(Level = LogLevel.Information,
        Message = "universe walk ({Trigger}) done: {Companies} companies in {Took}; {HandedOff} filings handed off, "
                  + "{Known} already stored, {Shared} stored under another company, {Refused} refused; "
                  + "{CompaniesRefused} companies refused, {Stopped} stopped")]
    private static partial void LogEnd(ILogger logger, string trigger, int companies, string took, int handedOff,
        int known, int shared, int refused, int companiesRefused, int stopped);
}
