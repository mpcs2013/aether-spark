using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using AetherSpark.Contracts;
using AetherSpark.Ingester.Persistence;
using AetherSpark.Ingester.Sec;
using AetherSpark.Ingester.Walk;
using Microsoft.Extensions.Logging;

namespace AetherSpark.Ingester.Tests;

/// <summary>
/// One company against a stand-in SEC that would answer anything (D5, ingester side): the ingester
/// requests only the URLs it builds, never a link, schema location or path found inside a filing;
/// a traversing file name and a header that disagrees with the index refuse their filing, the
/// others are stored, hashed and handed off with a valid contract; known filings cost no request.
/// </summary>
public sealed class CompanyFetcherTests
{
    private const long Cik = 1234567;
    private static readonly string Folder = "/Archives/edgar/data/1234567/";

    private static CancellationToken Token => TestContext.Current.CancellationToken;

    private sealed record StubFiling(
        string Accession, string Form, string Filed, string HeaderTime, string IndexTime, bool Xbrl, string Primary,
        IReadOnlyDictionary<string, string> Files, IReadOnlyList<string>? OtherNames = null);

    private static string Dashless(string accession) => accession.Replace("-", "", StringComparison.Ordinal);

    // A, B, H are handed off; C has a traversing schema name; D's header and index disagree; E is an
    // 8-K; F was filed before 2015; G is already stored. F and H are on the older page.
    private static readonly StubFiling A = new("0001234567-25-000001", "10-K", "2025-02-20", "20250220163000",
        "2025-02-20T21:30:00.000Z", true, "stub-20241231.htm",
        new Dictionary<string, string>
        {
            ["stub-20241231.htm"] = "<html><a href=\"{root}evil/link\">x</a><img src=\"{root}evil/img.png\"/>"
                                    + "<a href=\"file:///proc/self/environ\">y</a><a href=\"/run/secrets/sec-user-agent-contact\">z</a></html>",
            ["stub-20241231_htm.xml"] = "<xbrl><link:schemaRef xlink:href=\"{root}evil/remote.xsd\"/></xbrl>",
            ["stub-20241231.xsd"] = "<xs:schema><xs:import schemaLocation=\"{root}evil/import.xsd\"/>"
                                    + "<xs:include schemaLocation=\"../../../../run/secrets/x.xsd\"/></xs:schema>",
            ["stub-20241231_lab.xml"] = "<link:linkbase/>",
        },
        ["R1.htm", "Show.js", "../../escape.htm"]);

    private static readonly StubFiling B = new("0001234567-25-000002", "10-Q", "2025-08-01", "20250731180334",
        "2025-07-31T22:03:34.000Z", true, "stub-20250630.htm",
        new Dictionary<string, string>
        {
            ["stub-20250630.htm"] = "<html>q2</html>",
            ["stub-20250630_htm.xml"] = "<xbrl/>",
            ["stub-20250630.xsd"] = "<xs:schema/>",
        });

    private static readonly StubFiling C = new("0001234567-25-000003", "10-Q/A", "2025-09-01", "20250829100000",
        "2025-08-29T14:00:00.000Z", true, "stub-q2a.htm",
        new Dictionary<string, string> { ["stub-q2a.htm"] = "<html/>", ["stub-q2a_htm.xml"] = "<xbrl/>" },
        ["../../run/secrets/x.xsd"]);

    private static readonly StubFiling D = new("0001234567-25-000004", "10-Q", "2025-11-03", "20251103163000",
        "2025-11-03T21:00:00.000Z", true, "stub-q3.htm",
        new Dictionary<string, string> { ["stub-q3.htm"] = "<html/>" });

    private static readonly StubFiling E = new("0001234567-25-000005", "8-K", "2025-03-01", "20250301090000",
        "2025-03-01T14:00:00.000Z", false, "event.htm", new Dictionary<string, string> { ["event.htm"] = "<html/>" });

    private static readonly StubFiling F = new("0001234567-14-000001", "10-K", "2014-03-01", "20140301090000",
        "2014-03-01T14:00:00.000Z", false, "old.htm", new Dictionary<string, string> { ["old.htm"] = "<html/>" });

    private static readonly StubFiling G = new("0001234567-24-000009", "10-K", "2024-02-20", "20240220163000",
        "2024-02-20T21:30:00.000Z", false, "known.htm", new Dictionary<string, string> { ["known.htm"] = "<html/>" });

    private static readonly StubFiling H = new("0001234567-15-000001", "10-Q", "2015-05-01", "20150501120000",
        "2015-05-01T16:00:00.000Z", false, "q1.htm", new Dictionary<string, string> { ["q1.htm"] = "<html>2015</html>" });

    private static readonly string[] Tickers = ["STUB"];
    private static readonly string[] Exchanges = ["NYSE"];

    private static Dictionary<string, object> Columns(IEnumerable<StubFiling> filings)
    {
        var list = filings.ToList();
        return new Dictionary<string, object>
        {
            ["accessionNumber"] = list.Select(f => f.Accession),
            ["filingDate"] = list.Select(f => f.Filed),
            ["reportDate"] = list.Select(_ => ""),
            ["acceptanceDateTime"] = list.Select(f => f.IndexTime),
            ["form"] = list.Select(f => f.Form),
            ["primaryDocument"] = list.Select(f => f.Primary),
            ["isXBRL"] = list.Select(f => f.Xbrl ? 1 : 0),
            ["isInlineXBRL"] = list.Select(f => f.Xbrl ? 1 : 0),
        };
    }

    private static Dictionary<string, byte[]> Routes(Uri root)
    {
        static byte[] Utf8(string s) => Encoding.UTF8.GetBytes(s);
        var routes = new Dictionary<string, byte[]>(StringComparer.Ordinal)
        {
            ["/submissions/CIK0001234567.json"] = JsonSerializer.SerializeToUtf8Bytes(new
            {
                cik = "0001234567",
                name = "Stub Co",
                tickers = Tickers,
                exchanges = Exchanges,
                filings = new
                {
                    recent = Columns([D, C, B, E, A, G]),
                    files = new[]
                    {
                        new { name = "CIK0001234567-submissions-001.json", filingCount = 2, filingFrom = "2014-01-01", filingTo = "2015-06-30" },
                    },
                },
            }),
            ["/submissions/CIK0001234567-submissions-001.json"] = JsonSerializer.SerializeToUtf8Bytes(Columns([H, F])),
        };
        foreach (var f in new[] { A, B, C, D, E, F, G, H })
        {
            var folder = Folder + Dashless(f.Accession) + "/";
            var filed = DateOnly.Parse(f.Filed, CultureInfo.InvariantCulture).ToString("yyyyMMdd", CultureInfo.InvariantCulture);
            routes[folder + f.Accession + ".hdr.sgml"] = Utf8(
                $"<SEC-HEADER>{f.Accession}.hdr.sgml : {filed}\n<ACCEPTANCE-DATETIME>{f.HeaderTime}\n"
                + $"<ACCESSION-NUMBER>{f.Accession}\n<TYPE>{f.Form}\n<PUBLIC-DOCUMENT-COUNT>3\n<FILING-DATE>{filed}\n"
                + "<FILER>\n<COMPANY-DATA>\n<CONFORMED-NAME>Stub Co\n<CIK>0001234567\n");
            var names = f.Files.Keys.Concat(f.OtherNames ?? []).Select(n => new { name = n, type = "text.gif", size = "" });
            routes[folder + "index.json"] = JsonSerializer.SerializeToUtf8Bytes(new { directory = new { item = names } });
            foreach (var (name, content) in f.Files)
            {
                routes[folder + name] = Utf8(content.Replace("{root}", root.ToString(), StringComparison.Ordinal));
            }
        }

        return routes;
    }

    private static string Sha(byte[] bytes) => Convert.ToHexStringLower(SHA256.HashData(bytes));

    private sealed class Recorder(params string[] known) : IFilingRecorder
    {
        private int knownCalls;

        public List<(SubmissionRow Row, DateTimeOffset KnownAt, FilingJob Job)> Filings { get; } = [];

        public (string Name, IReadOnlyList<ListedTicker> Tickers)? Issuer { get; private set; }

        /// <summary>Filings another walk stores first: their record's write answers false.</summary>
        public HashSet<string> StoredElsewhere { get; init; } = new(StringComparer.Ordinal);

        /// <summary>Joint filings stored under another company from the start: accession number → its CIK.</summary>
        public Dictionary<string, long> OtherCompanies { get; init; } = new(StringComparer.Ordinal);

        /// <summary>As <see cref="OtherCompanies"/>, but stored only from the walk's second question on.</summary>
        public Dictionary<string, long> OtherCompaniesLater { get; init; } = new(StringComparer.Ordinal);

        /// <summary>The database fails after the walk's first question (the one before the filings).</summary>
        public bool FailsAfterTheFirstQuestion { get; init; }

        /// <summary>Every listing the fetcher recorded, as (CIK, accession number).</summary>
        public List<(long Cik, string AccessionNo)> Listings { get; } = [];

        public Task<IReadOnlyDictionary<string, long>> StoredUnderAsync(IReadOnlyCollection<string> accessionNos,
            CancellationToken cancellationToken)
        {
            var call = Interlocked.Increment(ref knownCalls);
            if (FailsAfterTheFirstQuestion && call > 1)
            {
                throw new InvalidOperationException("the database is gone");
            }

            // From the second question on, the filings another walk stored meanwhile are known too
            var ours = known.Concat(Filings.Select(f => f.Row.AccessionNo));
            var stored = (call > 1 ? ours.Concat(StoredElsewhere) : ours)
                .Select(a => KeyValuePair.Create(a, Cik))
                .Concat(OtherCompanies)
                .Concat(call > 1 ? OtherCompaniesLater : [])
                .Where(s => accessionNos.Contains(s.Key))
                .DistinctBy(s => s.Key, StringComparer.Ordinal)
                .ToDictionary(StringComparer.Ordinal);
            return Task.FromResult<IReadOnlyDictionary<string, long>>(stored);
        }

        public Task RecordListingsAsync(long cik, IReadOnlyCollection<string> accessionNos, CancellationToken cancellationToken)
        {
            Listings.AddRange(accessionNos.Select(a => (cik, a)));
            return Task.CompletedTask;
        }

        public Task RecordIssuerAsync(long cik, string name, IReadOnlyList<ListedTicker> tickers, CancellationToken cancellationToken)
        {
            Issuer = (name, tickers);
            return Task.CompletedTask;
        }

        public Task<bool> RecordFilingAsync(long cik, SubmissionRow row, DateTimeOffset knownAt, FilingJob job,
            CancellationToken cancellationToken)
        {
            if (StoredElsewhere.Contains(row.AccessionNo))
            {
                return Task.FromResult(false);
            }

            Filings.Add((row, knownAt, job));
            return Task.FromResult(true);
        }
    }

    private sealed class Captured : ILogger<CompanyFetcher>, ILogger<WalkProgress>
    {
        public List<string> Lines { get; } = [];

        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;

        public bool IsEnabled(LogLevel logLevel) => true;

        public void Log<TState>(LogLevel logLevel, EventId eventId, TState state, Exception? exception,
            Func<TState, Exception?, string> formatter)
        {
            lock (Lines)
            {
                Lines.Add($"{logLevel}: {formatter(state, exception)}");
            }
        }
    }

    private static (CompanyFetcher Fetcher, SecClient Client, SecPace Pace) Build(
        LoopbackSec sec, string raw, Recorder recorder, Captured log)
    {
        var settings = SecTesting.Settings(sec.Root, raw, TimeSpan.FromMilliseconds(5));
        var pace = new SecPace(TimeProvider.System, settings.RequestSpacing);
        var client = SecTesting.Client(settings, new SecStop(), pace);
        var progress = new WalkProgress(TimeProvider.System, log);
        return (new CompanyFetcher(client, new RawStore(settings), recorder, settings, progress, log), client, pace);
    }

    [Fact]
    public async Task RequestsOnlyItsBuiltUrlsAndHandsOffTheFilingsThatPassTheirChecks()
    {
        Dictionary<string, byte[]> routes = [];
        await using var sec = new LoopbackSec((r, _) =>
            routes.TryGetValue(r.Path, out var body) ? LoopbackAnswer.Ok(body) : LoopbackAnswer.Code(404));
        routes = Routes(sec.Root);
        var raw = SecTesting.TempFolder();
        var recorder = new Recorder(G.Accession);
        var log = new Captured();
        var (fetcher, client, pace) = Build(sec, raw, recorder, log);
        using (client)
        using (pace)
        {
            var result = await fetcher.FetchAsync(Cik, Token);

            Assert.Equal(new CompanyResult(HandedOff: 3, Known: 1, Refused: 2, Stopped: false), result);
        }

        // Only the URLs built from the CIK and the index's accession numbers and checked names
        string Path(StubFiling f, string name) => Folder + Dashless(f.Accession) + "/" + name;
        var expected = new HashSet<string>(StringComparer.Ordinal)
        {
            "/submissions/CIK0001234567.json",
            "/submissions/CIK0001234567-submissions-001.json",
        };
        foreach (var f in new[] { H, A, B, C })
        {
            expected.Add(Path(f, f.Accession + ".hdr.sgml"));
            expected.Add(Path(f, "index.json"));
        }

        expected.Add(Path(D, D.Accession + ".hdr.sgml"));
        foreach (var f in new[] { H, A, B })
        {
            foreach (var name in f.Files.Keys)
            {
                expected.Add(Path(f, name));
            }
        }

        Assert.Equal(expected, sec.Requests.Select(r => r.Path).ToHashSet(StringComparer.Ordinal));
        Assert.Equal(expected.Count, sec.Requests.Count);

        // Handed off oldest first, each with a valid contract whose hashes are the stored bytes'
        Assert.Equal([H.Accession, A.Accession, B.Accession], recorder.Filings.Select(f => f.Row.AccessionNo));
        foreach (var (row, knownAt, job) in recorder.Filings)
        {
            Assert.Empty(job.Validate());
            Assert.Equal(AcceptanceTime.ToContract(knownAt), job.KnownAt);
            foreach (var file in job.Files)
            {
                var stored = await File.ReadAllBytesAsync(System.IO.Path.Combine(raw, "1234567", row.AccessionNo, file.Name), Token);
                Assert.Equal(Sha(stored), file.Sha256);
                Assert.Equal(stored.Length, file.Size);
                Assert.Equal(routes[Path(new[] { A, B, H }.Single(f => f.Accession == row.AccessionNo), file.Name)], stored);
            }
        }

        var late = recorder.Filings.Single(f => f.Row.AccessionNo == B.Accession).Job;
        Assert.Equal("2025-07-31T22:03:34Z", late.KnownAt);
        Assert.Equal("2025-08-01", late.FiledDate);
        Assert.Equal(["primary"], recorder.Filings.Single(f => f.Row.AccessionNo == H.Accession).Job.Files.Select(f => f.Kind));
        Assert.Equal(["primary", "instance", "schema", "linkbase"],
            recorder.Filings.Single(f => f.Row.AccessionNo == A.Accession).Job.Files.Select(f => f.Kind));
        Assert.Equal("Stub Co", recorder.Issuer!.Value.Name);
        Assert.Equal([new ListedTicker("STUB", "NYSE")], recorder.Issuer.Value.Tickers);

        // Nothing but the filings' folders in the raw folder; refusals logged with their reasons
        Assert.Equal([H.Accession, A.Accession, B.Accession],
            Directory.GetDirectories(System.IO.Path.Combine(raw, "1234567")).Select(System.IO.Path.GetFileName).Order());
        Assert.Contains(log.Lines, l => l.Contains(C.Accession, StringComparison.Ordinal) && l.Contains("plain file name", StringComparison.Ordinal));
        Assert.Contains(log.Lines, l => l.Contains(D.Accession, StringComparison.Ordinal) && l.Contains("acceptance times differ", StringComparison.Ordinal));
        Assert.DoesNotContain(log.Lines, l => l.Contains(SecTesting.Contact, StringComparison.OrdinalIgnoreCase));
    }

    [Fact]
    public async Task ASecondRunChangesNothingAndAsksOnlyForTheIndex()
    {
        Dictionary<string, byte[]> routes = [];
        await using var sec = new LoopbackSec((r, _) =>
            routes.TryGetValue(r.Path, out var body) ? LoopbackAnswer.Ok(body) : LoopbackAnswer.Code(404));
        routes = Routes(sec.Root);
        var recorder = new Recorder(G.Accession);
        var (fetcher, client, pace) = Build(sec, SecTesting.TempFolder(), recorder, new Captured());
        using (client)
        using (pace)
        {
            await fetcher.FetchAsync(Cik, Token);
            var first = sec.Requests.Count;

            var again = await fetcher.FetchAsync(Cik, Token);

            Assert.Equal(new CompanyResult(HandedOff: 0, Known: 4, Refused: 2, Stopped: false), again);
            Assert.Equal(3, recorder.Filings.Count);

            // The two refused filings are asked again (they may be fixed); the stored ones are not
            var second = sec.Requests.Skip(first).Select(r => r.Path).ToList();
            Assert.DoesNotContain(second, p => p.Contains(Dashless(A.Accession), StringComparison.Ordinal)
                                              || p.Contains(Dashless(B.Accession), StringComparison.Ordinal)
                                              || p.Contains(Dashless(H.Accession), StringComparison.Ordinal));
        }
    }

    [Fact]
    public async Task TheStopEndsTheCompanyWithoutAnotherRequest()
    {
        Dictionary<string, byte[]> routes = [];
        await using var sec = new LoopbackSec((r, _) => r.Path.EndsWith(".hdr.sgml", StringComparison.Ordinal)
            ? LoopbackAnswer.Code(403)
            : routes.TryGetValue(r.Path, out var body) ? LoopbackAnswer.Ok(body) : LoopbackAnswer.Code(404));
        routes = Routes(sec.Root);
        var recorder = new Recorder();
        var (fetcher, client, pace) = Build(sec, SecTesting.TempFolder(), recorder, new Captured());
        using (client)
        using (pace)
        {
            var result = await fetcher.FetchAsync(Cik, Token);

            Assert.True(result.Stopped);
            Assert.Empty(recorder.Filings);
            Assert.Equal(3, sec.Requests.Count); // the index, its older page, the first header
        }
    }

    // B's schema is its last file: its primary document and instance are stored before it is asked for
    private static readonly string BSchema = Folder + Dashless(B.Accession) + "/stub-20250630.xsd";

    private static readonly Dictionary<string, string> Elsewhere = new(StringComparer.Ordinal) { ["Location"] = "http://127.0.0.1:9/elsewhere" };

    private static async Task<(CompanyResult Result, string[] Folders, Captured Log)> WalkAsync(
        Func<string, LoopbackAnswer?> answer, Recorder recorder)
    {
        Dictionary<string, byte[]> routes = [];
        await using var sec = new LoopbackSec((r, _) => answer(r.Path)
            ?? (routes.TryGetValue(r.Path, out var body) ? LoopbackAnswer.Ok(body) : LoopbackAnswer.Code(404)));
        routes = Routes(sec.Root);
        var raw = SecTesting.TempFolder();
        var log = new Captured();
        var (fetcher, client, pace) = Build(sec, raw, recorder, log);
        using (client)
        using (pace)
        {
            var result = await fetcher.FetchAsync(Cik, Token);
            var folders = Directory.GetDirectories(System.IO.Path.Combine(raw, "1234567"))
                .Select(f => System.IO.Path.GetFileName(f)!).Order(StringComparer.Ordinal).ToArray();
            return (result, folders, log);
        }
    }

    [Fact]
    public async Task AFilingRefusedWhileItsFilesAreStoredLeavesNoFiles()
    {
        var (result, folders, log) = await WalkAsync(
            p => p == BSchema ? LoopbackAnswer.Code(302, Elsewhere) : null, new Recorder(G.Accession));

        Assert.Equal(new CompanyResult(HandedOff: 2, Known: 1, Refused: 3, Stopped: false), result);
        Assert.Equal([H.Accession, A.Accession], folders);
        Assert.Contains(log.Lines, l => l.Contains(B.Accession, StringComparison.Ordinal) && l.Contains("files removed", StringComparison.Ordinal));
    }

    [Fact]
    public async Task TheStopWhileAFilingsFilesAreStoredRemovesThemToo()
    {
        var (result, folders, _) = await WalkAsync(
            p => p == BSchema ? LoopbackAnswer.Code(403) : null, new Recorder(G.Accession));

        Assert.True(result.Stopped);
        Assert.Equal([H.Accession, A.Accession], folders);
    }

    [Fact]
    public async Task AFilingAnotherWalkStoredFirstKeepsItsFiles()
    {
        // Refused at its record's write: the other walk's record names these files
        var atTheWrite = await WalkAsync(_ => null, new Recorder(G.Accession) { StoredElsewhere = { B.Accession } });
        Assert.Equal([H.Accession, A.Accession, B.Accession], atTheWrite.Folders);
        Assert.Contains(atTheWrite.Log.Lines, l => l.Contains(B.Accession, StringComparison.Ordinal) && l.Contains("already recorded", StringComparison.Ordinal));

        // Refused before it, but the database says it is recorded by now
        var before = await WalkAsync(
            p => p == BSchema ? LoopbackAnswer.Code(302, Elsewhere) : null,
            new Recorder(G.Accession) { StoredElsewhere = { B.Accession } });
        Assert.Equal([H.Accession, A.Accession, B.Accession], before.Folders);
        Assert.DoesNotContain(before.Log.Lines, l => l.Contains("files removed", StringComparison.Ordinal));
    }

    [Fact]
    public async Task AJointFilingStoredUnderAnotherCompanyCostsNoRequestAndItsCopyIsRemoved()
    {
        // A is a joint filing another company's walk stored first (as Dow Inc. with The Dow Chemical
        // Company); an earlier walk of this company fetched it again and left a copy in its own folder
        const long Other = 7654321;
        Dictionary<string, byte[]> routes = [];
        await using var sec = new LoopbackSec((r, _) =>
            routes.TryGetValue(r.Path, out var body) ? LoopbackAnswer.Ok(body) : LoopbackAnswer.Code(404));
        routes = Routes(sec.Root);
        var raw = SecTesting.TempFolder();
        var copy = System.IO.Path.Combine(raw, "1234567", A.Accession);
        var theirs = System.IO.Path.Combine(raw, "7654321", A.Accession);
        foreach (var folder in new[] { copy, theirs })
        {
            Directory.CreateDirectory(folder);
            await File.WriteAllTextAsync(System.IO.Path.Combine(folder, "stub-20241231.htm"), "<html/>", Token);
        }

        var recorder = new Recorder(G.Accession) { OtherCompanies = { [A.Accession] = Other } };
        var log = new Captured();
        var (fetcher, client, pace) = Build(sec, raw, recorder, log);
        using (client)
        using (pace)
        {
            var result = await fetcher.FetchAsync(Cik, Token);

            Assert.Equal(new CompanyResult(HandedOff: 2, Known: 1, Refused: 2, Stopped: false, Shared: 1), result);
        }

        // No request for it, no record, the listing recorded; the copy gone, the other company's folder kept
        Assert.DoesNotContain(sec.Requests, r => r.Path.Contains(Dashless(A.Accession), StringComparison.Ordinal));
        Assert.DoesNotContain(recorder.Filings, f => f.Row.AccessionNo == A.Accession);
        Assert.Contains((Cik, A.Accession), recorder.Listings);
        Assert.Contains((Cik, G.Accession), recorder.Listings);
        Assert.False(Directory.Exists(copy));
        Assert.True(File.Exists(System.IO.Path.Combine(theirs, "stub-20241231.htm")));
        Assert.Contains(log.Lines, l => l.Contains(
            $"filing {A.Accession}: its copy removed (the filing is stored under company {Other})", StringComparison.Ordinal));
        Assert.Contains(log.Lines, l => l.Contains(
            "2 filings handed off, 1 already stored, 1 stored under another company, 2 refused", StringComparison.Ordinal));
    }

    [Fact]
    public async Task AFilingStoredUnderAnotherCompanyAfterItsRefusalLeavesNoCopy()
    {
        // B is refused while its files are stored, and by then another company's walk has stored it:
        // the folder under this company's CIK is no one's, so it goes
        var (result, folders, log) = await WalkAsync(
            p => p == BSchema ? LoopbackAnswer.Code(302, Elsewhere) : null,
            new Recorder(G.Accession) { OtherCompaniesLater = { [B.Accession] = 7654321 } });

        Assert.Equal(new CompanyResult(HandedOff: 2, Known: 1, Refused: 3, Stopped: false), result);
        Assert.Equal([H.Accession, A.Accession], folders);
        Assert.Contains(log.Lines, l => l.Contains(B.Accession, StringComparison.Ordinal) && l.Contains("files removed", StringComparison.Ordinal));
    }

    [Fact]
    public async Task NothingIsRemovedWhenTheDatabaseCannotSay()
    {
        var (result, folders, log) = await WalkAsync(
            p => p == BSchema ? LoopbackAnswer.Code(302, Elsewhere) : null,
            new Recorder(G.Accession) { FailsAfterTheFirstQuestion = true });

        Assert.Equal(3, result.Refused);
        Assert.Contains(B.Accession, folders);
        Assert.Contains(log.Lines, l => l.Contains(B.Accession, StringComparison.Ordinal) && l.Contains("could not be removed", StringComparison.Ordinal));
    }
}
