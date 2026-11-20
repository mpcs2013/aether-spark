using System.Globalization;
using System.Text;

namespace AetherSpark.Ingester.Sec;

/// <summary>
/// The SEC's header of one filing (`{accession}.hdr.sgml`, about 1 KB): its top lines before the
/// first `&lt;FILER&gt;` block, one `&lt;TAG&gt;value` per line.
/// </summary>
/// <param name="AccessionNo">`ACCESSION-NUMBER`.</param>
/// <param name="Type">`TYPE`, the form.</param>
/// <param name="AcceptanceDateTime">`ACCEPTANCE-DATETIME`, Eastern local time (AcceptanceTime).</param>
/// <param name="FilingDate">`FILING-DATE`.</param>
public sealed record FilingHeader(string AccessionNo, string Type, string AcceptanceDateTime, DateOnly FilingDate)
{
    public const int MaxBytes = 64 * 1024;

    public static FilingHeader Parse(ReadOnlySpan<byte> bytes)
    {
        if (bytes.Length > MaxBytes)
        {
            throw new FormatException("the header is larger than 64 KiB");
        }

        var tags = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (var raw in Encoding.Latin1.GetString(bytes).Split('\n'))
        {
            var line = raw.TrimEnd('\r');
            if (line.StartsWith("<FILER>", StringComparison.Ordinal))
            {
                break;
            }

            var close = line.IndexOf('>', StringComparison.Ordinal);
            if (line.StartsWith('<') && close > 1)
            {
                tags.TryAdd(line[1..close], line[(close + 1)..].Trim());
            }
        }

        string Tag(string name) =>
            tags.TryGetValue(name, out var value) && value.Length > 0
                ? value
                : throw new FormatException($"the header has no {name}");

        if (!DateOnly.TryParseExact(Tag("FILING-DATE"), "yyyyMMdd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out var filed))
        {
            throw new FormatException("the header's FILING-DATE is not yyyyMMdd");
        }

        return new FilingHeader(Tag("ACCESSION-NUMBER"), Tag("TYPE"), Tag("ACCEPTANCE-DATETIME"), filed);
    }
}
