using System.Globalization;

namespace AetherSpark.Ingester.Sec;

/// <summary>
/// The only URLs the ingester requests (data-plane.md, section 3; egress.md): built from the
/// configured hosts, the CIK, and accession numbers and file names from the SEC's index for that
/// CIK, each checked. A URL found inside a filing is never followed.
/// </summary>
public static class SecUrls
{
    public static Uri Submissions(Uri data, long cik) =>
        Below(data, $"submissions/CIK{SecNames.Cik10(cik)}.json");

    public static Uri SubmissionsPage(Uri data, long cik, string page) =>
        SecNames.IsSubmissionsPage(page, cik)
            ? Below(data, $"submissions/{page}")
            : throw new ArgumentException("not a submissions page of this company", nameof(page));

    public static Uri FilingIndex(Uri archives, long cik, string accession) =>
        Below(archives, Folder(cik, accession) + "index.json");

    public static Uri Header(Uri archives, long cik, string accession) =>
        Below(archives, Folder(cik, accession) + accession + ".hdr.sgml");

    public static Uri File(Uri archives, long cik, string accession, string name) =>
        SecNames.IsFileName(name)
            ? Below(archives, Folder(cik, accession) + name)
            : throw new ArgumentException("not a valid file name", nameof(name));

    private static string Folder(long cik, string accession)
    {
        if (!SecNames.IsCik(cik))
        {
            throw new ArgumentOutOfRangeException(nameof(cik));
        }

        if (!SecNames.IsAccession(accession))
        {
            throw new ArgumentException("not an accession number", nameof(accession));
        }

        return string.Create(CultureInfo.InvariantCulture,
            $"Archives/edgar/data/{cik}/{accession.Replace("-", "", StringComparison.Ordinal)}/");
    }

    // The checked parts cannot climb, but the result is checked once more: same scheme, host and
    // port as the configured base, and below its path
    private static Uri Below(Uri root, string relative)
    {
        ArgumentNullException.ThrowIfNull(root);
        var url = new Uri(root, relative);
        var inside = url.Scheme == root.Scheme
            && string.Equals(url.Host, root.Host, StringComparison.OrdinalIgnoreCase)
            && url.Port == root.Port
            && url.AbsolutePath.StartsWith(root.AbsolutePath, StringComparison.Ordinal)
            && string.IsNullOrEmpty(url.Query) && string.IsNullOrEmpty(url.Fragment);
        return inside ? url : throw new ArgumentException("the URL leaves the SEC's folder", nameof(relative));
    }
}
