using AetherSpark.AppHost;
using Aspire.Hosting.ApplicationModel;
using Microsoft.Extensions.DependencyInjection;

// `aspire run`: the development loop (data-plane.md, decision 13 and PR 3b2b). The ingester walks a
// stand-in SEC, never the real one: `aspire run` is outside the egress control, so real filings are
// fetched only under `just up`. Aspire's core only, no hosting packages (owner, 2026-11-20): the
// AppHost has no lock file (ADR-0005 amendment 2), and the stack's own images and scripts run here.
var builder = DistributedApplication.CreateBuilder(args);
var repository = Path.GetFullPath(Path.Combine(builder.AppHostDirectory, "..", "..", ".."));

DevContainers.RemoveLeftovers();
var certificates = DevCertificates.Create("localhost", TimeProvider.System);
var run = DevRun.Prepare(certificates);

// The stack's postgres-data image, start script and migrations, with throw-away passwords and
// certificate placed in the container (never mounted from a folder) and this folder's pg_hba.conf.
// A new container and database at every start, the data in memory only (nothing left on disk); it
// runs as the image's postgres user (70).
const int Postgres = 70;
var postgresFolder = Path.Combine(repository, "deploy", "compose", "stack", "postgres-data");
var postgresName = $"aetherspark-dev-postgres-data-{Guid.NewGuid():N}"[..40];
builder.Services.AddHealthChecks().AddCheck("postgres-data-ready", new ReadyMarkerCheck(postgresName));
var postgres = builder.AddDockerfile("postgres-data", postgresFolder)
    .WithContainerName(postgresName)
    .WithEntrypoint("bash")
    .WithArgs("/postgres-data/run.sh")
    .WithContainerRuntimeArgs("--label", DevContainers.Label, "--user", $"{Postgres}:{Postgres}", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--shm-size", "256m",
        "--tmpfs", $"/var/lib/postgresql:uid={Postgres},gid={Postgres},mode=0700")
    .WithContainerFiles("/postgres-data", [
        new ContainerFile { Name = "run.sh", SourcePath = Path.Combine(postgresFolder, "run.sh") },
        new ContainerFile { Name = "migrate.sh", SourcePath = Path.Combine(postgresFolder, "migrate.sh") },
        new ContainerFile { Name = "pg_hba.conf", SourcePath = Path.Combine(builder.AppHostDirectory, "postgres-data", "pg_hba.conf") },
        new ContainerDirectory
        {
            Name = "migrations",
            Entries = ContainerDirectory.GetFileSystemItemsFromPath(Path.Combine(postgresFolder, "migrations")),
        },
    ], defaultOwner: 0, defaultGroup: 0)
    .WithContainerFiles("/run/secrets", run.DatabasePasswords.Select(p =>
        new ContainerFile { Name = p.Key, Contents = p.Value, Mode = UnixFileMode.UserRead }), defaultOwner: Postgres, defaultGroup: Postgres)
    .WithContainerFiles("/tls", [
        new ContainerFile { Name = "tls.crt", Contents = certificates.ServerPem, Mode = UnixFileMode.UserRead | UnixFileMode.GroupRead | UnixFileMode.OtherRead },
        new ContainerFile { Name = "tls.key", Contents = certificates.ServerKeyPem, Mode = UnixFileMode.UserRead },
    ], defaultOwner: Postgres, defaultGroup: Postgres)
    .WithEndpoint(targetPort: 5432, name: "postgres", scheme: "tcp")
    .WithHealthCheck("postgres-data-ready");

// The stack test's stand-in SEC (tests/stack/stub_sec.py) in the same pinned Python image
// (tests/egress/compose.layer3.yaml; tests/python/test_aspire_run.py keeps the two equal)
const int Nobody = 65534;
var secStub = builder.AddContainer("sec-stub", "python", "3.12.16-alpine3.24")
    .WithImageSHA256("e064671ca452259bab50e8e2c7bd896e39e9a343bb06ca25234adf6fe18d0fc7")
    .WithEntrypoint("python3")
    .WithArgs("-I", "/stub/stub_sec.py")
    .WithContainerName($"aetherspark-dev-sec-stub-{Guid.NewGuid():N}"[..36])
    .WithContainerRuntimeArgs("--label", DevContainers.Label, "--user", $"{Nobody}:{Nobody}", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true")
    .WithContainerFiles("/stub", [
        new ContainerFile { Name = "stub_sec.py", SourcePath = Path.Combine(repository, "tests", "stack", "stub_sec.py") },
    ], defaultOwner: 0, defaultGroup: 0)
    .WithContainerFiles("/", [
        new ContainerDirectory { Name = "log", Owner = Nobody, Group = Nobody, Mode = UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute },
    ])
    .WithHttpEndpoint(targetPort: 8080, name: "http");

// The ingester on this machine, pointed at the two: no proxy (the stand-in is local), the dummy
// contact, the throw-away root, this run's folder
var database = postgres.GetEndpoint("postgres");
var sec = secStub.GetEndpoint("http");
builder.AddProject<Projects.AetherSpark_Ingester>("ingester")
    .WithEnvironment("Ingester__DatabaseHost", "localhost")
    .WithEnvironment("Ingester__DatabasePort", $"{database.Property(EndpointProperty.Port)}")
    .WithEnvironment("Ingester__DatabasePasswordFile", run.IngesterPasswordFile)
    .WithEnvironment("Ingester__RootCertificateFile", run.RootCertificateFile)
    .WithEnvironment("Ingester__RawDirectory", run.RawDirectory)
    .WithEnvironment("Ingester__UniverseFile", run.UniverseFile)
    .WithEnvironment("Ingester__HeartbeatFile", run.HeartbeatFile)
    .WithEnvironment("Ingester__SecContactFile", run.ContactFile)
    .WithEnvironment("Ingester__SecArchives", sec)
    .WithEnvironment("Ingester__SecData", sec)
    .WithEnvironment("Ingester__Proxy", string.Empty)
    .WaitFor(postgres)
    .WaitFor(secStub);

builder.Build().Run();
