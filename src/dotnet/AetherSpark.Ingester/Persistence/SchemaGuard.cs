using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Weasel.Core.Migrations;
using Wolverine.Persistence.Durability;

namespace AetherSpark.Ingester.Persistence;

/// <summary>
/// The tables are the migration owner's (data-plane.md, decision 7), applied from SQL files by the
/// server's own start; this program cannot create or change one. So before it does any work it checks
/// that the database is what this build expects, and stops with the difference otherwise:
/// every EF Core migration recorded in meta.ef_migrations, and Wolverine's tables as its version
/// expects them (columns, keys and indexes).
/// </summary>
public static class SchemaGuard
{
    public static async Task CheckAsync(IServiceProvider services, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(services);

        await using (var scope = services.CreateAsyncScope())
        {
            var db = scope.ServiceProvider.GetRequiredService<IngestDbContext>();
            var pending = (await db.Database.GetPendingMigrationsAsync(cancellationToken).ConfigureAwait(false)).ToList();
            if (pending.Count > 0)
            {
                throw new InvalidOperationException(
                    $"the data Postgres lacks the ingester's migrations {string.Join(", ", pending)}: "
                    + "add their SQL file to deploy/compose/stack/postgres-data/migrations/");
            }
        }

        var store = services.GetRequiredService<IMessageStore>();
        if (store is not IDatabase database)
        {
            throw new InvalidOperationException($"Wolverine's message store {store.GetType().Name} is not a database");
        }

        await database.AssertDatabaseMatchesConfigurationAsync(cancellationToken).ConfigureAwait(false);
    }
}
