using System.Reflection;
using Ostium.Keycloak.Realm;

namespace AetherSpark.Realm.Tests;

/// <summary>
/// The aetherspark realm as AetherSpark declares it to Ostium's rules, and where its file is. The
/// only test file that names the realm file (the guard exempts this path, RealmGuardTests).
/// </summary>
internal static class Realm
{
    public const string FileName = "aetherspark-realm.json";
    public const string FilePath = "deploy/keycloak/" + FileName;

    /// <summary>The file's one placeholder; Keycloak's import and `just keycloak apply` fill it in.</summary>
    public const string PlacePlaceholder = "AETHERSPARK_PLACE";

    /// <summary>The place the checks fill in: the stack test's and CI's.</summary>
    public const string Place = "localhost";

    public static string RepositoryRoot { get; } = typeof(Realm).Assembly
        .GetCustomAttributes<AssemblyMetadataAttribute>()
        .Single(a => a.Key == "RepositoryRoot").Value
        ?? throw new InvalidOperationException("the build sets RepositoryRoot");

    public static string FullPath { get; } = Path.GetFullPath(Path.Combine(RepositoryRoot, FilePath));

    /// <summary>The file as committed, with its placeholder.</summary>
    public static string Text() => File.ReadAllText(FullPath);

    /// <summary>The file with the place filled in, as Keycloak imports it.</summary>
    public static string Rendered() => Text().Replace("${" + PlacePlaceholder + "}", Place, StringComparison.Ordinal);

    /// <summary>
    /// Today one client: Gate G3's test page behind forward-auth, confidential (oauth2-proxy holds
    /// the secret, so Ostium's BFF kind). No service account in this realm (the config account is
    /// master's), no owned claims, no protected audiences yet.
    /// </summary>
    public static RealmProfile Profile { get; } = new()
    {
        RealmName = "aetherspark",
        Clients =
        [
            new ClientProfile
            {
                ClientId = "forward-auth",
                Kind = ClientKind.Bff,
                RedirectUris = [$"https://whoami.aether.{Place}/oauth2/callback"],
                PostLogoutRedirectUris = [$"https://whoami.aether.{Place}/"],
                WebOrigins = [],
                SecretPlaceholder = "FORWARD_AUTH_CLIENT_SECRET",
            },
        ],
        ServiceAccounts = [],
        ClaimOwners = [],
        Placeholders = [PlacePlaceholder, "FORWARD_AUTH_CLIENT_SECRET"],
        ProtectedAudiences = [],
    };
}
