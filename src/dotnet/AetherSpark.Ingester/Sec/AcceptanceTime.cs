using System.Globalization;

namespace AetherSpark.Ingester.Sec;

/// <summary>
/// `known_at` (data-plane.md, section 2; ADR-0007 change 2): the SEC's acceptance time in UTC. The
/// filing's header states it in US Eastern local time without a zone (measured 2026-11-20 on six
/// filings), converted here with the daylight-saving rules of the time-zone database; the JSON
/// index's `acceptanceDateTime` is UTC and must agree to the second (D3). No zone label is trusted
/// on its own.
/// </summary>
public static class AcceptanceTime
{
    private static readonly TimeZoneInfo Eastern = FindEastern();

    /// <summary>The header's `yyyyMMddHHmmss` in Eastern time, as UTC.</summary>
    public static DateTimeOffset FromHeader(string value)
    {
        if (!DateTime.TryParseExact(value, "yyyyMMddHHmmss", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out var local))
        {
            throw new FormatException("the header's acceptance time is not yyyyMMddHHmmss");
        }

        // EDGAR accepts from 06:00 to 22:00 ET, never in the hour a clock change skips or repeats;
        // such a time is refused, not guessed
        if (Eastern.IsInvalidTime(local))
        {
            throw new FormatException($"the header's acceptance time {value} does not exist in US Eastern time");
        }

        if (Eastern.IsAmbiguousTime(local))
        {
            throw new FormatException($"the header's acceptance time {value} is ambiguous in US Eastern time");
        }

        return new DateTimeOffset(local, Eastern.GetUtcOffset(local)).ToUniversalTime();
    }

    /// <summary>The JSON index's `acceptanceDateTime` (`2024-08-01T22:03:34.000Z`), UTC.</summary>
    public static DateTimeOffset FromIndex(string value)
    {
        if (!DateTimeOffset.TryParseExact(value, "yyyy-MM-dd'T'HH:mm:ss.fff'Z'", CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var utc))
        {
            throw new FormatException("the index's acceptance time is not yyyy-MM-ddTHH:mm:ss.fffZ");
        }

        return utc;
    }

    /// <summary>The contract's form: UTC to the second with a Z.</summary>
    public static string ToContract(DateTimeOffset utc) =>
        utc.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture);

    // The IANA name on Linux (the image installs tzdata); Windows' own name where the IANA name is
    // not mapped (a test host without ICU)
    private static TimeZoneInfo FindEastern()
    {
        try
        {
            return TimeZoneInfo.FindSystemTimeZoneById("America/New_York");
        }
        catch (TimeZoneNotFoundException) when (OperatingSystem.IsWindows())
        {
            return TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time");
        }
    }
}
