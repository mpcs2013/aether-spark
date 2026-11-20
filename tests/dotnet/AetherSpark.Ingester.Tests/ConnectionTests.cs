using AetherSpark.Ingester.Persistence;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Npgsql;

namespace AetherSpark.Ingester.Tests;

public sealed class ConnectionTests : IDisposable
{
    private const string Password = "test-only-value-0d1f";
    private readonly string _folder = Directory.CreateTempSubdirectory("ingester-tests-").FullName;

    public void Dispose() => Directory.Delete(_folder, recursive: true);

    [Fact]
    public void TheDefaultsAreTheStacksContainerPaths()
    {
        var settings = IngesterSettings.FromConfiguration(Configuration([]));
        Assert.Equal("postgres-data.svc.aether.localhost", settings.DatabaseHost);
        Assert.Equal("/run/secrets/postgres-data-ingester-password", settings.DatabasePasswordFile);
        Assert.Equal("/ca-certs/root_ca.crt", settings.RootCertificateFile);
        Assert.Equal("/raw", settings.RawDirectory);
        Assert.Equal(new TimeOnly(4, 0), settings.DailyWalkUtc);
    }

    [Fact]
    public void ThePlaceNamesTheDatabaseHost()
    {
        var settings = IngesterSettings.FromConfiguration(Configuration(new() { ["AETHERSPARK_PLACE"] = "spark.example" }));
        Assert.Equal("postgres-data.svc.aether.spark.example", settings.DatabaseHost);
    }

    [Fact]
    public void TheConnectionChecksTheServerAgainstTheStackRootAndReadsThePasswordFile()
    {
        var file = Path.Combine(_folder, "password");
        File.WriteAllText(file, Password + "\n");
        var settings = new IngesterSettings { DatabaseHost = "postgres-data.svc.aether.localhost", DatabasePasswordFile = file };

        var builder = DataConnection.ConnectionString(settings);

        Assert.Equal(SslMode.VerifyFull, builder.SslMode);
        Assert.Equal("/ca-certs/root_ca.crt", builder.RootCertificate);
        Assert.Equal("ingester", builder.Username);
        Assert.Equal("research", builder.Database);
        Assert.Equal(Password, builder.Password);
        Assert.False(builder.PersistSecurityInfo);
    }

    [Fact]
    public void AnEmptyOrMissingSecretFileStopsWithItsNameOnly()
    {
        var empty = Path.Combine(_folder, "empty");
        File.WriteAllText(empty, "\n");
        var missing = Path.Combine(_folder, "missing");

        Assert.Contains("is empty", Assert.Throws<InvalidOperationException>(() => DataConnection.ReadSecret(empty)).Message, StringComparison.Ordinal);
        var error = Assert.Throws<InvalidOperationException>(() => DataConnection.ReadSecret(missing));
        Assert.Contains(missing, error.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void TheMigrationsCoverTheModel()
    {
        using var db = new IngestDbContext(DesignTimeFactory.Options());
        Assert.False(db.Database.HasPendingModelChanges(), "the model changed: add an EF migration and its SQL file");
    }

    [Fact]
    public void EveryMigrationHasItsSqlFile()
    {
        using var db = new IngestDbContext(DesignTimeFactory.Options());
        var folder = Path.Combine(UniverseFileTests.RepositoryRoot(), "deploy", "compose", "stack", "postgres-data", "migrations");
        var sql = string.Concat(Directory.GetFiles(folder, "*.sql").Select(File.ReadAllText));
        foreach (var migration in db.Database.GetMigrations())
        {
            Assert.Contains($"VALUES ('{migration}', ", sql, StringComparison.Ordinal);
        }
    }

    private static IConfiguration Configuration(Dictionary<string, string?> values) =>
        new ConfigurationBuilder().AddInMemoryCollection(values).Build();
}
