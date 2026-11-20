using System.Text.RegularExpressions;

namespace AetherSpark.Ingester.Sec;

/// <summary>
/// The User-Agent the SEC asks of automated clients: `AetherSpark` and the owner's email address
/// (an entered secret of the kind `contact`, owner 2026-11-19). It is sent with every request and
/// never written to a log, a trace or a file. The address is checked again here with the secret
/// store's form, so nothing that could end a header line reaches one.
/// </summary>
public sealed partial class SecIdentity
{
    public SecIdentity(string contact)
    {
        if (!ContactPattern().IsMatch(contact ?? ""))
        {
            throw new InvalidOperationException("the SEC contact is not one email address");
        }

        UserAgent = $"AetherSpark {contact}";
    }

    public string UserAgent { get; }

    public static SecIdentity FromFile(string path) => new(DataConnection.ReadSecret(path));

    // The secret store's kind `contact` (deploy/secret-store/secret_store.py, ENTERED)
    [GeneratedRegex(@"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(\.[A-Za-z0-9-]{1,63})+\z", RegexOptions.CultureInvariant)]
    private static partial Regex ContactPattern();

    // The contact never appears in a string form of this object
    public override string ToString() => "SecIdentity";
}
