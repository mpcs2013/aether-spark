using Ostium.Keycloak.Realm;

namespace AetherSpark.Realm.Tests;

/// <summary>The realm file against Ostium's per-client rules (the export checks; no Keycloak runs).</summary>
public sealed class RealmFileTests
{
    [Fact]
    public void TheRealmFileMeetsOstiumsClientRules()
    {
        var result = RealmChecks.CheckExport(Realm.Rendered(), Realm.Profile);
        Assert.True(result.Passed, result.ToString());
    }

    [Fact]
    public void TheRealmFileHoldsNoSecretAndOnlyDeclaredPlaceholders()
    {
        var result = RealmChecks.CheckExportSecrets(Realm.Text(), Realm.Profile);
        Assert.True(result.Passed, result.ToString());
    }

    // The checks catch a weakened file: each change breaks one rule
    [Theory]
    [InlineData("\"pkce.code.challenge.method\": \"S256\"", "\"pkce.code.challenge.method\": \"plain\"", "client.pkce-s256-required")]
    [InlineData("\"fullScopeAllowed\": false", "\"fullScopeAllowed\": true", "client.full-scope-off")]
    [InlineData("\"directAccessGrantsEnabled\": false", "\"directAccessGrantsEnabled\": true", "client.password-grant-off")]
    [InlineData("\"oauth2.device.authorization.grant.enabled\": \"false\"", "\"oauth2.device.authorization.grant.enabled\": \"true\"", "client.device-grant-off")]
    [InlineData("\"access.token.signed.response.alg\": \"RS256\"", "\"access.token.signed.response.alg\": \"HS256\"", "client.signature-algorithm")]
    [InlineData("\"webOrigins\": []", "\"webOrigins\": [\"+\"]", "client.web-origins-empty")]
    [InlineData("/oauth2/callback\"", "/oauth2/*\"", "client.redirect-uris-exact")]
    public void AWeakenedClientIsAFinding(string from, string to, string ruleId)
    {
        var text = Realm.Rendered();
        Assert.Contains(from, text, StringComparison.Ordinal);
        var result = RealmChecks.CheckExport(text.Replace(from, to, StringComparison.Ordinal), Realm.Profile);
        Assert.Contains(result.Findings, f => f.RuleId == ruleId);
    }

    [Fact]
    public void ASecretInTheRealmFileIsAFinding()
    {
        const string placeholder = "\"secret\": \"${FORWARD_AUTH_CLIENT_SECRET}\"";
        var text = Realm.Text();
        Assert.Contains(placeholder, text, StringComparison.Ordinal);
        text = text.Replace(placeholder, "\"secret\": \"not-a-placeholder\"", StringComparison.Ordinal);
        var result = RealmChecks.CheckExportSecrets(text, Realm.Profile);
        Assert.Contains(result.Findings, f => f.RuleId == "export.secret-not-placeholder");
    }
}
