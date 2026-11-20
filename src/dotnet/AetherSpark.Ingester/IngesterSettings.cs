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
        };
    }
}
