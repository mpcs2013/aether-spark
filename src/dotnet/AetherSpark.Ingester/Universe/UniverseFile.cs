using System.Globalization;

namespace AetherSpark.Ingester.Universe;

/// <summary>One company of the research universe (ADR-0020): its CIK, its name and why it is in the set.</summary>
public sealed record UniverseEntry(long Cik, string Name, string Why);

/// <summary>
/// The universe file (data-plane.md, decision 12): UTF-8 CSV with the header `cik,name,why`, one
/// company per line, no quoting; `why` may hold commas, `cik` and `name` may not. Lines starting with
/// `#` are comments. Any malformed line rejects the whole file: the ingester never guesses which
/// companies were meant.
/// </summary>
public static class UniverseFile
{
    public const string Header = "cik,name,why";

    public static IReadOnlyList<UniverseEntry> Read(string path) => Parse(File.ReadAllLines(path));

    public static IReadOnlyList<UniverseEntry> Parse(IEnumerable<string> lines)
    {
        ArgumentNullException.ThrowIfNull(lines);
        var entries = new List<UniverseEntry>();
        var seen = new HashSet<long>();
        var number = 0;
        var headerSeen = false;
        foreach (var raw in lines)
        {
            number++;
            var line = raw.TrimEnd('\r');
            if (line.Length == 0 || line.StartsWith('#'))
            {
                continue;
            }

            if (!headerSeen)
            {
                headerSeen = line == Header ? true : throw Bad(number, $"the first line must be `{Header}`");
                continue;
            }

            var parts = line.Split(',', 3);
            if (parts.Length != 3)
            {
                throw Bad(number, "three fields expected");
            }

            var (cikText, name, why) = (parts[0].Trim(), parts[1].Trim(), parts[2].Trim());
            if (cikText.Length is 0 or > 10 || !cikText.All(char.IsAsciiDigit)
                || !long.TryParse(cikText, NumberStyles.None, CultureInfo.InvariantCulture, out var cik) || cik == 0)
            {
                throw Bad(number, "the CIK must be 1 to 10 digits, not zero");
            }

            if (name.Length == 0 || why.Length == 0)
            {
                throw Bad(number, "name and why must not be empty");
            }

            if (!seen.Add(cik))
            {
                throw Bad(number, $"CIK {cik} is listed twice");
            }

            entries.Add(new UniverseEntry(cik, name, why));
        }

        return headerSeen ? entries : throw Bad(number, $"no `{Header}` header");
    }

    private static FormatException Bad(int line, string reason) =>
        new($"universe file, line {line}: {reason}");
}
