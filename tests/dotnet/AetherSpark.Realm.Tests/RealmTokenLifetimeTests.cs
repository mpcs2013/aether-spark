using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace AetherSpark.Realm.Tests;

/// <summary>
/// The access tokens' lifetime in the realm file, checked here and not only by Ostium's rules
/// (exchange thread O-044, 2026-11-20): Ostium.Keycloak.Realm 0.5.0 bounds it from above only, so a
/// realm value of -1 or 0 (Keycloak may read -1 as "until the session's maximum", hours instead of
/// minutes) passes its checks. AetherSpark's rule: the realm states `accessTokenLifespan` between 1
/// and 300 seconds, and a client that sets its own `access.token.lifespan` keeps it between 1 and
/// 300 too. Ostium's 0.6.0 added `realm.access-token-lifetime` (announced as 0.5.2); this check stays
/// as AetherSpark's own.
/// </summary>
public sealed class RealmTokenLifetimeTests
{
    private const int MaxSeconds = 300;

    /// <summary>What breaks the rule, by name; empty when the file keeps it.</summary>
    internal static List<string> Findings(string realmJson)
    {
        var findings = new List<string>();
        var realm = JsonNode.Parse(realmJson)?.AsObject() ?? throw new FormatException("the realm file is not a JSON object");

        if (realm["accessTokenLifespan"] is not JsonValue value || !value.TryGetValue<int>(out var seconds))
        {
            findings.Add("the realm does not state accessTokenLifespan as a whole number");
        }
        else if (seconds is < 1 or > MaxSeconds)
        {
            findings.Add($"the realm's accessTokenLifespan is {seconds}, not between 1 and {MaxSeconds}");
        }

        foreach (var client in realm["clients"]?.AsArray() ?? [])
        {
            var id = client?["clientId"]?.GetValue<string>() ?? "(no clientId)";
            if (client?["attributes"]?["access.token.lifespan"] is not { } own)
            {
                continue; // the realm's value applies
            }

            var text = own.GetValueKind() == JsonValueKind.String ? own.GetValue<string>() : own.ToJsonString();
            if (!int.TryParse(text, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out var clientSeconds)
                || clientSeconds is < 1 or > MaxSeconds)
            {
                findings.Add($"client {id}: access.token.lifespan is {text}, not between 1 and {MaxSeconds}");
            }
        }

        return findings;
    }

    [Fact]
    public void TheRealmFileKeepsAccessTokensToFiveMinutesAtMost()
    {
        Assert.Empty(Findings(Realm.Rendered()));
    }

    // The check catches each way out of the range, the gap of O-044 included
    [Theory]
    [InlineData("-1")]
    [InlineData("0")]
    [InlineData("301")]
    public void ARealmValueOutsideOneToThreeHundredIsAFinding(string value)
    {
        const string stated = "\"accessTokenLifespan\": 300";
        var text = Realm.Rendered();
        Assert.Contains(stated, text, StringComparison.Ordinal);

        var findings = Findings(text.Replace(stated, $"\"accessTokenLifespan\": {value}", StringComparison.Ordinal));

        Assert.Contains(findings, f => f.Contains($"is {value},", StringComparison.Ordinal));
    }

    [Fact]
    public void ARealmWithoutTheValueIsAFinding()
    {
        var realm = JsonNode.Parse(Realm.Rendered())!.AsObject();
        Assert.True(realm.Remove("accessTokenLifespan"));

        Assert.Contains(Findings(realm.ToJsonString()), f => f.Contains("does not state", StringComparison.Ordinal));
    }

    [Theory]
    [InlineData("-1")]
    [InlineData("0")]
    [InlineData("3600")]
    [InlineData("soon")]
    public void AClientsOwnValueOutsideOneToThreeHundredIsAFinding(string value)
    {
        var realm = JsonNode.Parse(Realm.Rendered())!.AsObject();
        var client = realm["clients"]!.AsArray().Single(c => c!["clientId"]!.GetValue<string>() == "forward-auth")!;
        client["attributes"]!["access.token.lifespan"] = value;

        Assert.Contains(Findings(realm.ToJsonString()), f => f.StartsWith("client forward-auth:", StringComparison.Ordinal));
    }

    [Fact]
    public void AClientsOwnValueWithinTheRangeIsKept()
    {
        var realm = JsonNode.Parse(Realm.Rendered())!.AsObject();
        var client = realm["clients"]!.AsArray().Single(c => c!["clientId"]!.GetValue<string>() == "forward-auth")!;
        client["attributes"]!["access.token.lifespan"] = "120";

        Assert.Empty(Findings(realm.ToJsonString()));
    }
}
