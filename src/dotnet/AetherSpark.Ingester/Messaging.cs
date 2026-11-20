using JasperFx;
using JasperFx.CodeGeneration;
using Wolverine;
using Wolverine.Postgresql;

namespace AetherSpark.Ingester;

/// <summary>
/// Wolverine's settings (data-plane.md, section 3): one node, durable local queues whose messages live
/// in `ingest_ops`, tables made by the migrations only, handler code generated at build time.
/// </summary>
public static class Messaging
{
    public const string Schema = "ingest_ops";

    public static void Configure(WolverineOptions opts, IngesterSettings settings)
    {
        ArgumentNullException.ThrowIfNull(opts);
        ArgumentNullException.ThrowIfNull(settings);

        opts.ServiceName = "ingester";
        opts.Durability.Mode = DurabilityMode.Solo;

        // The ingester's login has row rights only (decision 7): Wolverine creates nothing; its tables
        // come from the migration file built from the same settings (IngesterSchemaTests)
        opts.PersistMessagesWithPostgresql(DataConnection.ConnectionString(settings).ConnectionString, Schema);
        opts.AutoBuildMessageStorageOnStartup = AutoCreate.None;
        opts.Policies.UseDurableLocalQueues();

        // Handler code is written into the project at build time (`codegen write`) and loaded from
        // the assembly: no compiler at run time, and the image's file system stays read-only
        opts.CodeGeneration.TypeLoadMode = TypeLoadMode.Static;
    }
}
