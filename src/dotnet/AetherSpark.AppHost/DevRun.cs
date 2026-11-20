using System.Security.Cryptography;

namespace AetherSpark.AppHost;

/// <summary>
/// The files one `aspire run` needs on this machine, made new at every start in a folder of the
/// user's own (never the repository): the database passwords, the throw-away root, the raw folder,
/// the stand-in universe, a dummy SEC contact and the heartbeat. Nothing in it outlives the run's
/// purpose: the database is new at every start too, and holds only the stand-in SEC's company
/// (data-plane.md, decision 13).
/// </summary>
internal sealed class DevRun
{
    /// <summary>The stand-in SEC's one company (tests/stack/stub_sec.py).</summary>
    public const long StubCik = 1234567;

    /// <summary>A dummy contact, as in CI: the owner's sealed contact never reaches `aspire run`.</summary>
    public const string Contact = "aspire-run@example.org";

    /// <summary>The database's roles with a password (deploy/compose/stack/postgres-data/run.sh).</summary>
    private static readonly string[] Roles = ["superuser", "owner", "ingester", "pipeline"];

    private DevRun(string folder) => Folder = folder;

    public string Folder { get; }

    public string RawDirectory => Path.Combine(Folder, "raw");

    public string UniverseFile => Path.Combine(Folder, "universe.csv");

    public string ContactFile => Path.Combine(Folder, "secrets", "sec-user-agent-contact");

    public string IngesterPasswordFile => Path.Combine(Folder, "secrets", "postgres-data-ingester-password");

    public string RootCertificateFile => Path.Combine(Folder, "root_ca.crt");

    public string HeartbeatFile => Path.Combine(Folder, "heartbeat");

    /// <summary>The database's four passwords, by their secret names in the stack (compose.yaml).</summary>
    public IReadOnlyDictionary<string, string> DatabasePasswords { get; private init; } = new Dictionary<string, string>();

    /// <summary>
    /// Clears the previous run's folder and writes this run's files. Only the ingester's password,
    /// the root and the dummy contact land on disk; the other passwords and the server's key go
    /// into the database's container only.
    /// </summary>
    public static DevRun Prepare(DevCertificates certificates)
    {
        ArgumentNullException.ThrowIfNull(certificates);
        var folder = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData, Environment.SpecialFolderOption.Create),
            "AetherSpark",
            "aspire-run");
        if (Directory.Exists(folder))
        {
            Directory.Delete(folder, recursive: true);
        }

        var passwords = Roles.ToDictionary(
            role => $"postgres-data-{role}-password",
            _ => RandomNumberGenerator.GetHexString(64, lowercase: true));
        var run = new DevRun(folder) { DatabasePasswords = passwords };

        CreateFolder(folder);
        CreateFolder(Path.Combine(folder, "secrets"));
        CreateFolder(run.RawDirectory);
        WritePrivate(run.IngesterPasswordFile, passwords["postgres-data-ingester-password"]);
        WritePrivate(run.ContactFile, Contact);
        WritePrivate(run.RootCertificateFile, certificates.RootPem);
        WritePrivate(run.UniverseFile, $"cik,name,why\n{StubCik},Stub Co,aspire run's stand-in company (tests/stack/stub_sec.py)\n");
        return run;
    }

    private static void CreateFolder(string path)
    {
        if (OperatingSystem.IsWindows())
        {
            // Under %LOCALAPPDATA%, whose access list admits this user (and the system) only
            Directory.CreateDirectory(path);
        }
        else
        {
            Directory.CreateDirectory(path, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
        }
    }

    private static void WritePrivate(string path, string contents)
    {
        var options = new FileStreamOptions { Mode = FileMode.CreateNew, Access = FileAccess.Write };
        if (!OperatingSystem.IsWindows())
        {
            options.UnixCreateMode = UnixFileMode.UserRead | UnixFileMode.UserWrite;
        }

        // No byte-order mark: the files are read as plain values
        using var writer = new StreamWriter(path, new System.Text.UTF8Encoding(false), options);
        writer.Write(contents);
    }

    public override string ToString() => $"DevRun {Folder}";
}
