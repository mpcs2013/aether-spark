using AetherSpark.Ingester;
using AetherSpark.Ingester.Persistence;
using AetherSpark.Ingester.Walk;
using AetherSpark.ServiceDefaults;
using JasperFx;
using Microsoft.EntityFrameworkCore;
using Wolverine;

var builder = Host.CreateApplicationBuilder(args);
builder.AddServiceDefaults();

var settings = IngesterSettings.FromConfiguration(builder.Configuration);
builder.Services.AddSingleton(settings);
builder.Services.AddSingleton(TimeProvider.System);
builder.Services.AddSingleton(_ => DataConnection.Build(settings));
builder.Services.AddDbContext<IngestDbContext>((services, options) =>
    options.UseNpgsql(services.GetRequiredService<Npgsql.NpgsqlDataSource>(), DesignTimeFactory.Configure));

builder.UseWolverine(opts => Messaging.Configure(opts, settings));

builder.Services.AddHostedService<Heartbeat>();
builder.Services.AddHostedService<WalkTimer>();

var host = builder.Build();

// A plain start (no command) checks the database first; Wolverine's commands (`codegen write`,
// `db-dump`) run at build time without a server
if (args.Length == 0)
{
    await SchemaGuard.CheckAsync(host.Services, CancellationToken.None).ConfigureAwait(false);
}

return await host.RunJasperFxCommands(args).ConfigureAwait(false);
