using Microsoft.Extensions.Configuration;

namespace AetherSpark.Ingester;

/// <summary>
/// Where the ingester finds its database, secrets, certificates and folders. The defaults are the
/// stack's container paths (deploy/compose/stack/compose.yaml); `aspire run` overrides them.
/// </summary>
public sealed record IngesterSettings
{
    /// <summary>The data Postgres's service name; its certificate is checked against it.</summary>
    public required string DatabaseHost { get; init; }

    public int DatabasePort { get; init; } = 5432;

    public string DatabaseName { get; init; } = "research";

    public string DatabaseUser { get; init; } = "ingester";

    /// <summary>The file that holds the database password (a secret file, NFR-SEC-04).</summary>
    public string DatabasePasswordFile { get; init; } = "/run/secrets/postgres-data-ingester-password";

    /// <summary>The stack CA's root, the only trust anchor for the database's certificate.</summary>
    public string RootCertificateFile { get; init; } = "/ca-certs/root_ca.crt";

    /// <summary>The raw SEC downloads (data-plane.md, decision 9).</summary>
    public string RawDirectory { get; init; } = "/raw";

    /// <summary>The universe file: one company per line (CIK, name, why it is in the set).</summary>
    public string UniverseFile { get; init; } = "/universe/universe.csv";

    /// <summary>Touched after each successful database check; the container's health check reads its age.</summary>
    public string HeartbeatFile { get; init; } = "/tmp/heartbeat";

    /// <summary>The daily walk's time of day, in UTC: after EDGAR's 22:00 ET close.</summary>
    public TimeOnly DailyWalkUtc { get; init; } = new(4, 0);

    /// <summary>The owner's contact for the SEC's User-Agent (an entered secret, data-plane.md decision 8).</summary>
    public string SecContactFile { get; init; } = "/run/secrets/sec-user-agent-contact";

    /// <summary>The runtime proxy (egress.md); none in the unit tests and against the stub SEC.</summary>
    public Uri? Proxy { get; init; }

    /// <summary>EDGAR's archive host; the stack test points it at its stub SEC.</summary>
    public Uri SecArchives { get; init; } = new("https://www.sec.gov/");

    /// <summary>EDGAR's data host (the submissions index).</summary>
    public Uri SecData { get; init; } = new("https://data.sec.gov/");

    /// <summary>The first filing date fetched: FY2015 on (data-plane.md, decision 12).</summary>
    public DateOnly FiledFrom { get; init; } = new(2015, 1, 1);

    /// <summary>
    /// The least time between two requests to the SEC (it allows 10 per second): 4 sent per second, so
    /// the network can bunch arrivals by a quarter second before the SEC counts 5 in one second. 210 ms
    /// left 50 ms, and a new connection's set-up alone took 60 ms on a CI runner (PR #119).
    /// </summary>
    public TimeSpan RequestSpacing { get; init; } = TimeSpan.FromMilliseconds(250);

    /// <summary>The first retry's wait; it grows exponentially, with jitter.</summary>
    public TimeSpan RetryDelay { get; init; } = TimeSpan.FromSeconds(2);

    /// <summary>The largest file stored; a larger one refuses its filing.</summary>
    public long MaxFileBytes { get; init; } = 256L * 1024 * 1024;

    public static IngesterSettings FromConfiguration(IConfiguration configuration)
    {
        ArgumentNullException.ThrowIfNull(configuration);
        var section = configuration.GetSection("Ingester");
        var place = configuration["AETHERSPARK_PLACE"];
        var host = section["DatabaseHost"]
            ?? $"postgres-data.svc.aether.{(string.IsNullOrWhiteSpace(place) ? "localhost" : place)}";
        var defaults = new IngesterSettings { DatabaseHost = host };
        return defaults with
        {
            DatabasePort = section.GetValue("DatabasePort", defaults.DatabasePort),
            DatabaseName = section["DatabaseName"] ?? defaults.DatabaseName,
            DatabaseUser = section["DatabaseUser"] ?? defaults.DatabaseUser,
            DatabasePasswordFile = section["DatabasePasswordFile"] ?? defaults.DatabasePasswordFile,
            RootCertificateFile = section["RootCertificateFile"] ?? defaults.RootCertificateFile,
            RawDirectory = section["RawDirectory"] ?? defaults.RawDirectory,
            UniverseFile = section["UniverseFile"] ?? defaults.UniverseFile,
            HeartbeatFile = section["HeartbeatFile"] ?? defaults.HeartbeatFile,
            DailyWalkUtc = section.GetValue("DailyWalkUtc", defaults.DailyWalkUtc),
            SecContactFile = section["SecContactFile"] ?? defaults.SecContactFile,
            Proxy = string.IsNullOrWhiteSpace(section["Proxy"]) ? null : new Uri(section["Proxy"]!, UriKind.Absolute),
            SecArchives = Folder(section["SecArchives"]) ?? defaults.SecArchives,
            SecData = Folder(section["SecData"]) ?? defaults.SecData,
            FiledFrom = section.GetValue("FiledFrom", defaults.FiledFrom),
            RequestSpacing = section.GetValue("RequestSpacing", defaults.RequestSpacing),
            RetryDelay = section.GetValue("RetryDelay", defaults.RetryDelay),
            MaxFileBytes = section.GetValue("MaxFileBytes", defaults.MaxFileBytes) is var max and > 0 and <= 1L << 30
                ? max
                : throw new InvalidOperationException("Ingester:MaxFileBytes must be between 1 byte and 1 GiB"),
        };
    }

    // A base address ends with a slash, so relative paths stay below it
    private static Uri? Folder(string? value) =>
        string.IsNullOrWhiteSpace(value) ? null : new Uri(value.EndsWith('/') ? value : value + "/", UriKind.Absolute);
}
