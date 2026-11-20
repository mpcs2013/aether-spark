using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;

namespace AetherSpark.AppHost;

/// <summary>
/// A throw-away certificate authority and the development database's certificate, made at every
/// start of the AppHost (owner, 2026-11-20). The ingester keeps its one way to the database:
/// encrypted, the certificate checked by name (localhost) and against this root only. The keys live
/// in memory; the server's key goes into its container only, the root's is dropped when the
/// AppHost has signed.
/// </summary>
internal sealed record DevCertificates(string RootPem, string ServerPem, string ServerKeyPem)
{
    /// <summary>Long enough for a working week; the next start makes new ones.</summary>
    private static readonly TimeSpan Lifetime = TimeSpan.FromDays(7);

    public static DevCertificates Create(string serverName, TimeProvider time)
    {
        var from = time.GetUtcNow().AddMinutes(-5);
        var until = from + Lifetime;

        using var rootKey = ECDsa.Create(ECCurve.NamedCurves.nistP256);
        var rootRequest = new CertificateRequest("CN=AetherSpark aspire run root (throw-away)", rootKey, HashAlgorithmName.SHA256);
        rootRequest.CertificateExtensions.Add(new X509BasicConstraintsExtension(true, true, 0, true));
        rootRequest.CertificateExtensions.Add(new X509KeyUsageExtension(X509KeyUsageFlags.KeyCertSign | X509KeyUsageFlags.CrlSign, true));
        rootRequest.CertificateExtensions.Add(new X509SubjectKeyIdentifierExtension(rootRequest.PublicKey, false));
        using var root = rootRequest.CreateSelfSigned(from, until);

        using var serverKey = ECDsa.Create(ECCurve.NamedCurves.nistP256);
        var serverRequest = new CertificateRequest($"CN={serverName}", serverKey, HashAlgorithmName.SHA256);
        serverRequest.CertificateExtensions.Add(new X509BasicConstraintsExtension(false, false, 0, true));
        serverRequest.CertificateExtensions.Add(new X509KeyUsageExtension(X509KeyUsageFlags.DigitalSignature, true));
        serverRequest.CertificateExtensions.Add(new X509EnhancedKeyUsageExtension([new Oid("1.3.6.1.5.5.7.3.1", "serverAuth")], false));
        var names = new SubjectAlternativeNameBuilder();
        names.AddDnsName(serverName);
        serverRequest.CertificateExtensions.Add(names.Build(true));
        serverRequest.CertificateExtensions.Add(X509AuthorityKeyIdentifierExtension.CreateFromCertificate(root, true, false));
        using var server = serverRequest.Create(root, from, until, RandomNumberGenerator.GetBytes(16));

        return new DevCertificates(root.ExportCertificatePem(), server.ExportCertificatePem(), serverKey.ExportPkcs8PrivateKeyPem());
    }

    /// <summary>Never the key in a log or a debugger's summary.</summary>
    public override string ToString() => "DevCertificates";
}
