using System.Reflection;
using AetherSpark.Ingester.Universe;

namespace AetherSpark.Ingester.Tests;

public sealed class UniverseFileTests
{
    [Fact]
    public void ReadsCompaniesAndSkipsCommentsAndBlankLines()
    {
        var entries = UniverseFile.Parse([
            "# a comment",
            "cik,name,why",
            "",
            "320193,Apple Inc.,inline XBRL since 2019, many segments",
            "0000789019,Microsoft Corp,fiscal year ends in June\r",
        ]);

        Assert.Equal(
            [
                new UniverseEntry(320193, "Apple Inc.", "inline XBRL since 2019, many segments"),
                new UniverseEntry(789019, "Microsoft Corp", "fiscal year ends in June"),
            ],
            entries);
    }

    [Fact]
    public void TheShippedFileIsValid()
    {
        var path = Path.Combine(RepositoryRoot(), "deploy", "universe", "universe.csv");
        _ = UniverseFile.Read(path);
    }

    [Fact]
    public void AHeaderOnlyFileIsAnEmptyUniverse() => Assert.Empty(UniverseFile.Parse(["cik,name,why"]));

    [Theory]
    [InlineData("name,cik,why", "first line")]
    [InlineData("cik,name,why\n320193,Apple Inc.", "three fields")]
    [InlineData("cik,name,why\n32019a,Apple Inc.,x", "1 to 10 digits")]
    [InlineData("cik,name,why\n12345678901,Too long,x", "1 to 10 digits")]
    [InlineData("cik,name,why\n0,Zero,x", "not zero")]
    [InlineData("cik,name,why\n-1,Negative,x", "1 to 10 digits")]
    [InlineData("cik,name,why\n320193,,x", "must not be empty")]
    [InlineData("cik,name,why\n320193,Apple Inc., ", "must not be empty")]
    [InlineData("cik,name,why\n320193,Apple Inc.,x\n0000320193,Apple again,y", "listed twice")]
    [InlineData("# only a comment", "no `cik,name,why` header")]
    public void AMalformedFileIsRejectedWhole(string text, string reason)
    {
        var error = Assert.Throws<FormatException>(() => UniverseFile.Parse(text.Split('\n')));
        Assert.Contains(reason, error.Message, StringComparison.Ordinal);
    }

    internal static string RepositoryRoot() => typeof(UniverseFileTests).Assembly
        .GetCustomAttributes<AssemblyMetadataAttribute>()
        .Single(a => a.Key == "RepositoryRoot").Value
        ?? throw new InvalidOperationException("the build sets RepositoryRoot");
}
