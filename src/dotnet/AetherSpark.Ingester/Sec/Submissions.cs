using System.Globalization;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace AetherSpark.Ingester.Sec;

/// <summary>One filing as the submissions index lists it.</summary>
public sealed record SubmissionRow(
    string AccessionNo,
    string Form,
    DateOnly FilingDate,
    DateOnly? ReportDate,
    string AcceptanceDateTime,
    string PrimaryDocument,
    bool IsXbrl,
    bool IsInlineXbrl);

/// <summary>An older page of the index (`filings.files`), with the filing dates it covers.</summary>
public sealed record SubmissionsPage(string Name, DateOnly FilingFrom, DateOnly FilingTo);

/// <summary>A ticker as the index lists it today; the exchange is empty when it gives none.</summary>
public sealed record ListedTicker(string Symbol, string Exchange);

/// <summary>
/// A company's submissions index (data.sec.gov/submissions/CIK##########.json): its name, today's
/// tickers, the recent filings and the older pages. The filings are columns of equal length; a
/// row whose accession number or dates do not have their form is left out and counted.
/// </summary>
public sealed partial record Submissions(
    long Cik,
    string Name,
    IReadOnlyList<ListedTicker> Tickers,
    IReadOnlyList<SubmissionRow> Filings,
    IReadOnlyList<SubmissionsPage> Pages,
    int RowsLeftOut)
{
    public const int MaxBytes = 64 * 1024 * 1024;

    public static Submissions Parse(ReadOnlySpan<byte> json)
    {
        using var document = Open(json);
        var root = document.RootElement;
        var cikText = Text(root, "cik");
        if (!long.TryParse(cikText, NumberStyles.None, CultureInfo.InvariantCulture, out var cik) || !SecNames.IsCik(cik))
        {
            throw new FormatException("the index's cik is not a CIK");
        }

        var name = Text(root, "name").Trim();
        if (name.Length is 0 or > 300 || name.Any(char.IsControl))
        {
            throw new FormatException("the index's company name is empty, too long or has control characters");
        }

        var filings = Member(root, "filings", JsonValueKind.Object);
        var (rows, leftOut) = ReadRows(Member(filings, "recent", JsonValueKind.Object));
        var pages = new List<SubmissionsPage>();
        if (filings.TryGetProperty("files", out var files) && files.ValueKind == JsonValueKind.Array)
        {
            foreach (var page in files.EnumerateArray())
            {
                var pageName = Text(page, "name");
                if (!SecNames.IsSubmissionsPage(pageName, cik))
                {
                    throw new FormatException("an older page of the index is not named after this company");
                }

                pages.Add(new SubmissionsPage(pageName, Date(Text(page, "filingFrom")), Date(Text(page, "filingTo"))));
            }
        }

        return new Submissions(cik, name, ReadTickers(root), rows, pages, leftOut);
    }

    /// <summary>An older page: the same columns as `filings.recent`, at the top.</summary>
    public static (IReadOnlyList<SubmissionRow> Rows, int LeftOut) ParsePage(ReadOnlySpan<byte> json)
    {
        using var document = Open(json);
        return ReadRows(document.RootElement);
    }

    private static JsonDocument Open(ReadOnlySpan<byte> json)
    {
        if (json.Length > MaxBytes)
        {
            throw new FormatException("the index is larger than 64 MiB");
        }

        var reader = new Utf8JsonReader(json, new JsonReaderOptions { MaxDepth = 16 });
        return JsonDocument.ParseValue(ref reader);
    }

    private static (IReadOnlyList<SubmissionRow>, int) ReadRows(JsonElement columns)
    {
        var accession = Column(columns, "accessionNumber");
        var form = Column(columns, "form");
        var filingDate = Column(columns, "filingDate");
        var reportDate = Column(columns, "reportDate");
        var accepted = Column(columns, "acceptanceDateTime");
        var primary = Column(columns, "primaryDocument");
        var xbrl = Column(columns, "isXBRL");
        var inline = Column(columns, "isInlineXBRL");
        var count = accession.Count;
        if (new[] { form, filingDate, reportDate, accepted, primary, xbrl, inline }.Any(c => c.Count != count))
        {
            throw new FormatException("the index's filing columns differ in length");
        }

        var rows = new List<SubmissionRow>(count);
        var leftOut = 0;
        for (var i = 0; i < count; i++)
        {
            var acc = accession[i].GetString();
            var formText = form[i].GetString();
            if (!SecNames.IsAccession(acc) || string.IsNullOrEmpty(formText)
                || !TryDate(filingDate[i].GetString(), out var filed))
            {
                leftOut++;
                continue;
            }

            DateOnly? report = TryDate(reportDate[i].GetString(), out var r) ? r : null;
            rows.Add(new SubmissionRow(acc!, formText, filed, report, accepted[i].GetString() ?? "",
                primary[i].GetString() ?? "", Flag(xbrl[i]), Flag(inline[i])));
        }

        return (rows, leftOut);
    }

    private static List<ListedTicker> ReadTickers(JsonElement root)
    {
        var tickers = new List<ListedTicker>();
        if (!root.TryGetProperty("tickers", out var symbols) || symbols.ValueKind != JsonValueKind.Array)
        {
            return tickers;
        }

        var exchanges = root.TryGetProperty("exchanges", out var e) && e.ValueKind == JsonValueKind.Array
            ? e.EnumerateArray().ToList()
            : [];
        var i = 0;
        foreach (var symbol in symbols.EnumerateArray())
        {
            var exchange = i < exchanges.Count && exchanges[i].ValueKind == JsonValueKind.String ? exchanges[i].GetString()! : "";
            i++;
            if (symbol.ValueKind == JsonValueKind.String && TickerPattern().IsMatch(symbol.GetString()!)
                && ExchangePattern().IsMatch(exchange))
            {
                tickers.Add(new ListedTicker(symbol.GetString()!, exchange));
            }
        }

        return tickers.Distinct().ToList();
    }

    private static List<JsonElement> Column(JsonElement columns, string name) =>
        Member(columns, name, JsonValueKind.Array).EnumerateArray().ToList();

    private static JsonElement Member(JsonElement element, string name, JsonValueKind kind) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(name, out var value) && value.ValueKind == kind
            ? value
            : throw new FormatException($"the index has no {name}");

    private static string Text(JsonElement element, string name) => Member(element, name, JsonValueKind.String).GetString()!;

    private static bool Flag(JsonElement value) =>
        value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var flag) && flag == 1;

    private static DateOnly Date(string text) =>
        TryDate(text, out var date) ? date : throw new FormatException("the index has a date that is not yyyy-MM-dd");

    private static bool TryDate(string? text, out DateOnly date) =>
        DateOnly.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out date);

    [GeneratedRegex(@"^[A-Za-z0-9.-]{1,16}\z", RegexOptions.CultureInvariant)]
    private static partial Regex TickerPattern();

    [GeneratedRegex(@"^[A-Za-z0-9 .&-]{0,40}\z", RegexOptions.CultureInvariant)]
    private static partial Regex ExchangePattern();
}
