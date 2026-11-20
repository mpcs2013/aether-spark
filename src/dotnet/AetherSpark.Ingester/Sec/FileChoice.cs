using System.Text.Json;

namespace AetherSpark.Ingester.Sec;

/// <summary>A file the ingester stores, with its kind in the job contract.</summary>
public sealed record ChosenFile(string Name, string Kind);

/// <summary>
/// Which of a filing's files the ingester stores (data-plane.md, section 3): the primary document
/// and, for an XBRL filing, the instance, the extension schemas and the linkbases that exist. The
/// SEC's folder index gives names only (no kinds, no hashes; sizes often empty), and the set
/// differs between filers: Microsoft's linkbases sit inside its schema, Tesla's 10-K/A has two of
/// four (measured 2026-11-20). A chosen name that is not a plain file name refuses the filing; the
/// other names are never requested.
/// </summary>
public static class FileChoice
{
    public const int MaxIndexBytes = 4 * 1024 * 1024;
    public const int MaxFiles = 64;

    private static readonly string[] LinkbaseEnds = ["_cal.xml", "_def.xml", "_lab.xml", "_pre.xml"];

    /// <summary>The names in a filing folder's `index.json`.</summary>
    public static IReadOnlyList<string> ParseIndex(ReadOnlySpan<byte> json)
    {
        if (json.Length > MaxIndexBytes)
        {
            throw new FormatException("the folder index is larger than 4 MiB");
        }

        var reader = new Utf8JsonReader(json, new JsonReaderOptions { MaxDepth = 8 });
        using var document = JsonDocument.ParseValue(ref reader);
        if (!document.RootElement.TryGetProperty("directory", out var directory)
            || !directory.TryGetProperty("item", out var items) || items.ValueKind != JsonValueKind.Array)
        {
            throw new FormatException("the folder index has no directory.item list");
        }

        return items.EnumerateArray()
            .Select(i => i.TryGetProperty("name", out var n) && n.ValueKind == JsonValueKind.String ? n.GetString()! : "")
            .Where(n => n.Length > 0)
            .ToList();
    }

    /// <summary>The files to store, or the reason the filing is refused.</summary>
    public static (IReadOnlyList<ChosenFile> Files, string? Refusal) Choose(IReadOnlyList<string> names, SubmissionRow row)
    {
        ArgumentNullException.ThrowIfNull(names);
        ArgumentNullException.ThrowIfNull(row);
        var chosen = new List<ChosenFile>();
        if (!names.Contains(row.PrimaryDocument, StringComparer.Ordinal))
        {
            return ([], "the primary document is not in the folder index");
        }

        chosen.Add(new ChosenFile(row.PrimaryDocument, "primary"));
        if (row.IsXbrl)
        {
            var instances = names.Where(n => IsInstance(n, row.IsInlineXbrl)).ToList();
            if (instances.Count != 1)
            {
                return ([], $"{instances.Count} XBRL instances in the folder index, expected one");
            }

            var schemas = names.Where(n => n.EndsWith(".xsd", StringComparison.OrdinalIgnoreCase)).ToList();
            if (schemas.Count == 0)
            {
                return ([], "an XBRL filing without an extension schema");
            }

            chosen.Add(new ChosenFile(instances[0], "instance"));
            chosen.AddRange(schemas.Select(n => new ChosenFile(n, "schema")));
            chosen.AddRange(names.Where(IsLinkbase).Select(n => new ChosenFile(n, "linkbase")));
        }

        var bad = chosen.FirstOrDefault(f => !SecNames.IsFileName(f.Name));
        if (bad is not null)
        {
            return ([], "a chosen file's name is not a plain file name");
        }

        if (chosen.Count > MaxFiles || chosen.DistinctBy(f => f.Name, StringComparer.Ordinal).Count() != chosen.Count)
        {
            return ([], "the chosen files are too many or repeat");
        }

        return (chosen, null);
    }

    private static bool IsLinkbase(string name) =>
        LinkbaseEnds.Any(end => name.EndsWith(end, StringComparison.OrdinalIgnoreCase));

    // Inline XBRL: the SEC's extracted instance, `<document>_htm.xml`. Plain XBRL: the one .xml that
    // is neither a linkbase nor the SEC's own FilingSummary.xml
    private static bool IsInstance(string name, bool inline) =>
        inline
            ? name.EndsWith("_htm.xml", StringComparison.OrdinalIgnoreCase)
            : name.EndsWith(".xml", StringComparison.OrdinalIgnoreCase) && !IsLinkbase(name)
              && !string.Equals(name, "FilingSummary.xml", StringComparison.OrdinalIgnoreCase);
}
