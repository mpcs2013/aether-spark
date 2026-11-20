using System.Text.Json;
using System.Text.Json.Nodes;
using AetherSpark.Contracts;

namespace AetherSpark.Ingester.Tests;

/// <summary>
/// The generated C# contract judges the shared cases (contracts/examples/filing-job.cases.json)
/// exactly as the generated Pydantic model does (contracts/tests/test_contracts.py).
/// </summary>
public sealed class ContractTests
{
    private static readonly JsonElement Cases = JsonDocument.Parse(File.ReadAllText(
        Path.Combine(UniverseFileTests.RepositoryRoot(), "contracts", "examples", "filing-job.cases.json"))).RootElement;

    public static TheoryData<string> Valid => [.. Cases.GetProperty("valid").EnumerateObject().Select(p => p.Name)];

    public static TheoryData<string> Invalid => [.. Cases.GetProperty("invalid").EnumerateObject().Select(p => p.Name)];

    [Theory]
    [MemberData(nameof(Valid))]
    public void TheValidCasesHoldAndRoundTrip(string name)
    {
        var json = Cases.GetProperty("valid").GetProperty(name).GetRawText();

        var job = JsonSerializer.Deserialize<FilingJob>(json)!;

        Assert.Empty(job.Validate());
        Assert.True(JsonNode.DeepEquals(JsonNode.Parse(json), JsonNode.Parse(JsonSerializer.Serialize(job))));
    }

    [Theory]
    [MemberData(nameof(Invalid))]
    public void TheInvalidCasesFail(string name)
    {
        var json = Cases.GetProperty("invalid").GetProperty(name).GetRawText();

        FilingJob? job;
        try
        {
            job = JsonSerializer.Deserialize<FilingJob>(json);
        }
        catch (JsonException)
        {
            return;
        }

        Assert.NotNull(job);
        Assert.NotEmpty(job.Validate());
    }
}
