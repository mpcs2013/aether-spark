using System.Globalization;
using System.Text.RegularExpressions;

namespace AetherSpark.Ingester.Sec;

/// <summary>
/// The forms of every value taken from the SEC's answers before it goes into a URL, a path or the
/// database (data-plane.md, section 3). The file name is stricter than the database's check: it never
/// starts with a dot, so `.` and `..` cannot pass.
/// </summary>
public static partial class SecNames
{
    public const long MaxCik = 9_999_999_999;

    /// <summary>The forms the ingester fetches: annual and quarterly reports and their amendments.</summary>
    public static readonly IReadOnlySet<string> Forms = new HashSet<string>(StringComparer.Ordinal)
    {
        "10-K", "10-Q", "10-K/A", "10-Q/A",
    };

    public static bool IsCik(long cik) => cik is >= 1 and <= MaxCik;

    public static bool IsAccession(string? value) => value is not null && AccessionPattern().IsMatch(value);

    public static bool IsFileName(string? value) => value is not null && FileNamePattern().IsMatch(value);

    /// <summary>An older page of a company's submissions index, named after the same CIK.</summary>
    public static bool IsSubmissionsPage(string? value, long cik) =>
        value is not null && SubmissionsPagePattern().Match(value) is { Success: true } m
        && long.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture) == cik;

    public static string Cik10(long cik) =>
        IsCik(cik) ? cik.ToString("D10", CultureInfo.InvariantCulture) : throw new ArgumentOutOfRangeException(nameof(cik));

    [GeneratedRegex(@"^[0-9]{10}-[0-9]{2}-[0-9]{6}\z", RegexOptions.CultureInvariant)]
    private static partial Regex AccessionPattern();

    [GeneratedRegex(@"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,199}\z", RegexOptions.CultureInvariant)]
    private static partial Regex FileNamePattern();

    [GeneratedRegex(@"^CIK([0-9]{10})-submissions-[0-9]{3}\.json\z", RegexOptions.CultureInvariant)]
    private static partial Regex SubmissionsPagePattern();
}
