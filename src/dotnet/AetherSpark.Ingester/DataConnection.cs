using Npgsql;

namespace AetherSpark.Ingester;

/// <summary>
/// The data Postgres's connection, built in code from the secret file (data-plane.md, decision 8):
/// the password never passes through an environment variable or a connection string in
/// configuration, and the server's certificate is checked by name and against the stack's root only.
/// </summary>
public static class DataConnection
{
    public static NpgsqlConnectionStringBuilder ConnectionString(IngesterSettings settings)
    {
        ArgumentNullException.ThrowIfNull(settings);
        return new NpgsqlConnectionStringBuilder
        {
            Host = settings.DatabaseHost,
            Port = settings.DatabasePort,
            Database = settings.DatabaseName,
            Username = settings.DatabaseUser,
            Password = ReadSecret(settings.DatabasePasswordFile),
            SslMode = SslMode.VerifyFull,
            RootCertificate = settings.RootCertificateFile,
            ApplicationName = "ingester",
            PersistSecurityInfo = false,
        };
    }

    public static NpgsqlDataSource Build(IngesterSettings settings) =>
        new NpgsqlDataSourceBuilder(ConnectionString(settings).ConnectionString).Build();

    /// <summary>A secret file's value without its line end; the error names the file, never the value.</summary>
    public static string ReadSecret(string path)
    {
        string value;
        try
        {
            value = File.ReadAllText(path).TrimEnd('\r', '\n');
        }
        catch (IOException e)
        {
            throw new InvalidOperationException($"secret file {path} cannot be read", e);
        }
        catch (UnauthorizedAccessException e)
        {
            throw new InvalidOperationException($"secret file {path} cannot be read", e);
        }

        return value.Length > 0 ? value : throw new InvalidOperationException($"secret file {path} is empty");
    }
}
