using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Design;

namespace AetherSpark.Ingester.Persistence;

/// <summary>
/// For `dotnet ef migrations add` and the SQL file's generation only: no server is contacted, so the
/// connection string names none and holds no password.
/// </summary>
public sealed class DesignTimeFactory : IDesignTimeDbContextFactory<IngestDbContext>
{
    public IngestDbContext CreateDbContext(string[] args) => new(Options());

    public static DbContextOptions<IngestDbContext> Options() =>
        new DbContextOptionsBuilder<IngestDbContext>()
            .UseNpgsql("Host=design-time-only", Configure)
            .Options;

    /// <summary>The settings the program and the design-time tools share.</summary>
    public static void Configure(Npgsql.EntityFrameworkCore.PostgreSQL.Infrastructure.NpgsqlDbContextOptionsBuilder npgsql)
    {
        ArgumentNullException.ThrowIfNull(npgsql);
        npgsql.MigrationsHistoryTable(IngestDbContext.HistoryTable, IngestDbContext.HistorySchema);
    }
}
