using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using AetherSpark.Ingester.Sec;

namespace AetherSpark.Ingester.Tests;

/// <summary>
/// The SEC's answers as the ingester reads them, on public excerpts fetched 2026-11-20
/// (Fixtures/sec/sources.json): D3's acceptance time, the submissions index, the folder index and
/// the choice of files, the URLs, and the raw store.
/// </summary>
public sealed class SecParsingTests
{
    private static readonly string Fixtures =
        Path.Combine(UniverseFileTests.RepositoryRoot(), "tests", "dotnet", "AetherSpark.Ingester.Tests", "Fixtures", "sec");

    private static byte[] Fixture(string name) => File.ReadAllBytes(Path.Combine(Fixtures, name));

    // D3: the header's Eastern time, converted with the clock rules, equals the JSON index's UTC value
    // for six real filings: winter (EST) and summer (EDT), two days after the 2026 autumn change,
    // and one accepted after 17:30 ET whose filing date is the next day
    [Theory]
    [InlineData("0000320193-26-000026.hdr.sgml", "2026-11-03T13:05:15.000Z", "10-K", "2026-11-03")]
    [InlineData("0000320193-26-000006.hdr.sgml", "2026-01-30T11:01:32.000Z", "10-Q", "2026-01-30")]
    [InlineData("0000320193-26-000020.hdr.sgml", "2026-07-31T10:01:02.000Z", "10-Q", "2026-07-31")]
    [InlineData("0001193125-26-323660.hdr.sgml", "2026-07-29T20:08:01.000Z", "10-K", "2026-07-29")]
    [InlineData("0001104659-26-053166.hdr.sgml", "2026-04-30T21:13:17.000Z", "10-K/A", "2026-04-30")]
    [InlineData("0000320193-24-000081.hdr.sgml", "2024-08-01T22:03:34.000Z", "10-Q", "2024-08-02")]
    public void TheHeadersEasternTimeEqualsTheIndexsUtc(string file, string index, string form, string filed)
    {
        var header = FilingHeader.Parse(Fixture(file));

        Assert.Equal(file.Replace(".hdr.sgml", "", StringComparison.Ordinal), header.AccessionNo);
        Assert.Equal(form, header.Type);
        Assert.Equal(DateOnly.Parse(filed, System.Globalization.CultureInfo.InvariantCulture), header.FilingDate);
        var knownAt = AcceptanceTime.FromHeader(header.AcceptanceDateTime);
        Assert.Equal(AcceptanceTime.FromIndex(index), knownAt);
        Assert.Equal(TimeSpan.Zero, knownAt.Offset);
    }

    [Fact]
    public void AFilingAcceptedAfterTheCutOffKeepsItsTrueTime()
    {
        var knownAt = AcceptanceTime.FromHeader(FilingHeader.Parse(Fixture("0000320193-24-000081.hdr.sgml")).AcceptanceDateTime);

        Assert.Equal("2024-08-01T22:03:34Z", AcceptanceTime.ToContract(knownAt));
    }

    [Theory]
    [InlineData("20260308013000", "2026-03-08T06:30:00Z")] // before the spring change: EST
    [InlineData("20260308030000", "2026-03-08T07:00:00Z")] // just after it: EDT
    [InlineData("20261101003000", "2026-11-01T04:30:00Z")] // before the autumn change: EDT
    [InlineData("20261101020000", "2026-11-01T07:00:00Z")] // just after it: EST
    [InlineData("20150302173000", "2015-03-02T22:30:00Z")] // the period's first year
    public void TheClockChangesAreApplied(string header, string utc) =>
        Assert.Equal(utc, AcceptanceTime.ToContract(AcceptanceTime.FromHeader(header)));

    [Theory]
    [InlineData("20260308023000")] // skipped in spring
    [InlineData("20261101013000")] // repeated in autumn
    [InlineData("2026-11-03 08:05")]
    [InlineData("")]
    public void ATimeThatDoesNotExistOrIsAmbiguousIsRefused(string header) =>
        Assert.Throws<FormatException>(() => AcceptanceTime.FromHeader(header));

    [Theory]
    [InlineData("2024-08-01T22:03:34Z")]
    [InlineData("2024-08-01T18:03:34.000-04:00")]
    [InlineData("2024-08-01 22:03:34")]
    public void TheIndexsTimeMustHaveItsExactForm(string index) =>
        Assert.Throws<FormatException>(() => AcceptanceTime.FromIndex(index));

    [Fact]
    public void AHeaderWithoutItsTagsIsRefused()
    {
        Assert.Throws<FormatException>(() => FilingHeader.Parse("<SEC-HEADER>x\n<TYPE>10-K\n"u8));
        Assert.Throws<FormatException>(() => FilingHeader.Parse(new byte[FilingHeader.MaxBytes + 1]));
    }

    [Fact]
    public void TheSubmissionsIndexGivesTheCompanyItsFilingsAndOlderPages()
    {
        var submissions = Submissions.Parse(Fixture("apple-submissions-excerpt.json"));

        Assert.Equal(320193, submissions.Cik);
        Assert.Equal("Apple Inc.", submissions.Name);
        Assert.Equal([new ListedTicker("AAPL", "Nasdaq")], submissions.Tickers);
        Assert.Equal(6, submissions.Filings.Count);
        Assert.Equal(0, submissions.RowsLeftOut);
        var late = Assert.Single(submissions.Filings, r => r.AccessionNo == "0000320193-24-000081");
        Assert.Equal("10-Q", late.Form);
        Assert.Equal(new DateOnly(2024, 8, 2), late.FilingDate);
        Assert.Equal("2024-08-01T22:03:34.000Z", late.AcceptanceDateTime);
        Assert.Equal("aapl-20240629.htm", late.PrimaryDocument);
        Assert.True(late.IsXbrl && late.IsInlineXbrl);
        var page = Assert.Single(submissions.Pages);
        Assert.Equal("CIK0000320193-submissions-001.json", page.Name);
        Assert.Equal(new DateOnly(2015, 10, 5), page.FilingTo);
    }

    [Fact]
    public void AMalformedRowIsLeftOutAndCounted()
    {
        var text = Encoding.UTF8.GetString(Fixture("apple-submissions-excerpt.json"))
            .Replace("0000320193-24-000081", "0000320193-24-00008X", StringComparison.Ordinal);

        var submissions = Submissions.Parse(Encoding.UTF8.GetBytes(text));

        Assert.Equal(5, submissions.Filings.Count);
        Assert.Equal(1, submissions.RowsLeftOut);
    }

    [Fact]
    public void AnOlderPageNamedAfterAnotherCompanyRefusesTheIndex()
    {
        var text = Encoding.UTF8.GetString(Fixture("apple-submissions-excerpt.json"))
            .Replace("CIK0000320193-submissions-001.json", "CIK0000789019-submissions-001.json", StringComparison.Ordinal);

        Assert.Throws<FormatException>(() => Submissions.Parse(Encoding.UTF8.GetBytes(text)));
    }

    private static SubmissionRow Row(string primary, bool xbrl = true, bool inline = true) =>
        new("0000320193-26-000006", "10-Q", new DateOnly(2026, 1, 30), null, "2026-01-30T11:01:32.000Z", primary, xbrl, inline);

    [Fact]
    public void AppleStoresAllFourLinkbases()
    {
        var (files, refusal) = FileChoice.Choose(
            FileChoice.ParseIndex(Fixture("apple-10q-0000320193-26-000006.index.json")), Row("aapl-20251227.htm"));

        Assert.Null(refusal);
        Assert.Equal(
            [
                new ChosenFile("aapl-20251227.htm", "primary"),
                new ChosenFile("aapl-20251227_htm.xml", "instance"),
                new ChosenFile("aapl-20251227.xsd", "schema"),
                new ChosenFile("aapl-20251227_cal.xml", "linkbase"),
                new ChosenFile("aapl-20251227_def.xml", "linkbase"),
                new ChosenFile("aapl-20251227_lab.xml", "linkbase"),
                new ChosenFile("aapl-20251227_pre.xml", "linkbase"),
            ],
            files);
    }

    [Fact]
    public void MicrosoftsLinkbasesSitInItsSchema()
    {
        var (files, refusal) = FileChoice.Choose(
            FileChoice.ParseIndex(Fixture("microsoft-10k-0001193125-26-323660.index.json")), Row("msft-20260630.htm"));

        Assert.Null(refusal);
        Assert.Equal(["primary", "instance", "schema"], files.Select(f => f.Kind));
        Assert.Equal("msft-20260630_htm.xml", files[1].Name);
    }

    [Fact]
    public void TeslasAmendmentHasTwoLinkbases()
    {
        var (files, refusal) = FileChoice.Choose(
            FileChoice.ParseIndex(Fixture("tesla-10ka-0001104659-26-053166.index.json")), Row("tm2611837d1_10ka.htm"));

        Assert.Null(refusal);
        Assert.Equal(["tsla-20251231_lab.xml", "tsla-20251231_pre.xml"],
            files.Where(f => f.Kind == "linkbase").Select(f => f.Name));
    }

    [Fact]
    public void AFilingWithoutXbrlStoresItsPrimaryDocumentOnly()
    {
        var (files, refusal) = FileChoice.Choose(["paper.htm", "R1.htm", "x.xsd"], Row("paper.htm", xbrl: false, inline: false));

        Assert.Null(refusal);
        Assert.Equal([new ChosenFile("paper.htm", "primary")], files);
    }

    [Fact]
    public void APlainXbrlFilingFindsItsInstanceAmongTheXmlFiles()
    {
        var (files, refusal) = FileChoice.Choose(
            ["a.htm", "a-20151231.xml", "a-20151231.xsd", "a-20151231_lab.xml", "FilingSummary.xml"],
            Row("a.htm", xbrl: true, inline: false));

        Assert.Null(refusal);
        Assert.Equal("a-20151231.xml", Assert.Single(files, f => f.Kind == "instance").Name);
    }

    [Theory]
    [InlineData("missing.htm", "a_htm.xml|a.xsd", "primary document")]
    [InlineData("a.htm", "a.xsd", "0 XBRL instances")]
    [InlineData("a.htm", "a_htm.xml|b_htm.xml|a.xsd", "2 XBRL instances")]
    [InlineData("a.htm", "a_htm.xml", "without an extension schema")]
    [InlineData("a.htm", "a_htm.xml|../../run/secrets/x.xsd", "plain file name")]
    [InlineData("a.htm", "a_htm.xml|a.xsd|/etc/x_lab.xml", "plain file name")]
    public void AFilingIsRefusedWhenItsFilesDoNotFit(string primary, string others, string reason)
    {
        var names = new List<string> { "a.htm" };
        names.AddRange(others.Split('|'));

        var (files, refusal) = FileChoice.Choose(names, Row(primary));

        Assert.Empty(files);
        Assert.Contains(reason, refusal, StringComparison.Ordinal);
    }

    [Fact]
    public void NamesThatAreNotChosenAreIgnored()
    {
        var (files, refusal) = FileChoice.Choose(
            ["a.htm", "a_htm.xml", "a.xsd", "../../escape.htm", "file:///proc/self/environ"], Row("a.htm"));

        Assert.Null(refusal);
        Assert.Equal(3, files.Count);
    }

    [Fact]
    public void TheUrlsAreBuiltFromCheckedPartsOnly()
    {
        var www = new Uri("https://www.sec.gov/");
        var data = new Uri("https://data.sec.gov/");

        Assert.Equal("https://data.sec.gov/submissions/CIK0000320193.json", SecUrls.Submissions(data, 320193).ToString());
        Assert.Equal("https://data.sec.gov/submissions/CIK0000320193-submissions-001.json",
            SecUrls.SubmissionsPage(data, 320193, "CIK0000320193-submissions-001.json").ToString());
        Assert.Equal("https://www.sec.gov/Archives/edgar/data/320193/000032019324000081/index.json",
            SecUrls.FilingIndex(www, 320193, "0000320193-24-000081").ToString());
        Assert.Equal("https://www.sec.gov/Archives/edgar/data/320193/000032019324000081/0000320193-24-000081.hdr.sgml",
            SecUrls.Header(www, 320193, "0000320193-24-000081").ToString());
        Assert.Equal("https://www.sec.gov/Archives/edgar/data/320193/000032019324000081/aapl-20240629_htm.xml",
            SecUrls.File(www, 320193, "0000320193-24-000081", "aapl-20240629_htm.xml").ToString());
    }

    [Theory]
    [InlineData("..")]
    [InlineData(".hidden")]
    [InlineData("../x.xml")]
    [InlineData("a/b.xml")]
    [InlineData("a%2fb.xml")]
    [InlineData("a.xml?x=1")]
    [InlineData("a.xml#x")]
    [InlineData("https://example.org/x.xsd")]
    [InlineData("a.xml\n")]
    public void AFileNameThatIsNotPlainBuildsNoUrl(string name) =>
        Assert.Throws<ArgumentException>(() => SecUrls.File(new Uri("https://www.sec.gov/"), 320193, "0000320193-24-000081", name));

    [Fact]
    public void ABadCikAccessionOrPageBuildsNoUrl()
    {
        var www = new Uri("https://www.sec.gov/");
        Assert.Throws<ArgumentOutOfRangeException>(() => SecUrls.Header(www, 0, "0000320193-24-000081"));
        Assert.Throws<ArgumentOutOfRangeException>(() => SecUrls.Header(www, 10_000_000_000, "0000320193-24-000081"));
        Assert.Throws<ArgumentException>(() => SecUrls.Header(www, 320193, "0000320193-24-00008"));
        Assert.Throws<ArgumentException>(() => SecUrls.Header(www, 320193, "../../../etc"));
        Assert.Throws<ArgumentException>(() => SecUrls.SubmissionsPage(www, 320193, "CIK0000789019-submissions-001.json"));
    }

    [Fact]
    public async Task TheRawStoreHashesWhatItStoresAndKeepsItInsideTheFilingsFolder()
    {
        var root = SecTesting.TempFolder();
        var store = new RawStore(new IngesterSettings { DatabaseHost = "x", RawDirectory = root, MaxFileBytes = 100 });
        var bytes = Encoding.UTF8.GetBytes("<html>filing</html>");

        var stored = await store.WriteAsync(320193, "0000320193-24-000081", new ChosenFile("a.htm", "primary"),
            new MemoryStream(bytes), TestContext.Current.CancellationToken);

        var path = Path.Combine(root, "320193", "0000320193-24-000081", "a.htm");
        Assert.Equal(bytes, await File.ReadAllBytesAsync(path, TestContext.Current.CancellationToken));
        Assert.Equal(Convert.ToHexStringLower(SHA256.HashData(bytes)), stored.Sha256);
        Assert.Equal(bytes.Length, stored.Size);
        Assert.Single(Directory.GetFiles(Path.GetDirectoryName(path)!, "*", SearchOption.AllDirectories));

        await Assert.ThrowsAsync<FileTooLargeException>(() => store.WriteAsync(320193, "0000320193-24-000081",
            new ChosenFile("big.htm", "primary"), new MemoryStream(new byte[101]), TestContext.Current.CancellationToken));
        Assert.False(File.Exists(Path.Combine(root, "320193", "0000320193-24-000081", "big.htm")));
        Assert.Single(Directory.GetFiles(Path.GetDirectoryName(path)!, "*", SearchOption.AllDirectories));

        await Assert.ThrowsAsync<ArgumentException>(() => store.WriteAsync(320193, "0000320193-24-000081",
            new ChosenFile("../escape.htm", "primary"), new MemoryStream(bytes), TestContext.Current.CancellationToken));
    }

    [Fact]
    public void TheFixturesAreTheSecsBytes()
    {
        using var sources = JsonDocument.Parse(Fixture("sources.json"));
        foreach (var source in sources.RootElement.GetProperty("files").EnumerateArray())
        {
            if (!source.GetProperty("excerpt").GetBoolean())
            {
                var name = source.GetProperty("file").GetString()!;
                Assert.Equal(source.GetProperty("sha256").GetString(), Convert.ToHexStringLower(SHA256.HashData(Fixture(name))));
            }
        }
    }
}
