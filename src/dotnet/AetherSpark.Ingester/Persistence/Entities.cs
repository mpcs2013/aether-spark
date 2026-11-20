namespace AetherSpark.Ingester.Persistence;

/// <summary>A company, keyed by its CIK (ADR-0020); its name as the SEC's index last gave it.</summary>
public sealed class Issuer
{
    public long Cik { get; set; }

    public required string Name { get; set; }

    public DateTimeOffset FirstSeenAt { get; set; }

    public DateTimeOffset UpdatedAt { get; set; }
}

/// <summary>A ticker with its validity: a row, never a key (data-plane.md, section 1).</summary>
public sealed class Ticker
{
    public long Cik { get; set; }

    public required string Symbol { get; set; }

    /// <summary>The exchange as the SEC names it; empty when the index gives none.</summary>
    public required string Exchange { get; set; }

    public DateOnly ValidFrom { get; set; }

    public DateOnly? ValidTo { get; set; }
}

/// <summary>One filing, by accession number: its form and its two dates.</summary>
public sealed class Filing
{
    public required string AccessionNo { get; set; }

    public long Cik { get; set; }

    public required string Form { get; set; }

    /// <summary>The SEC's acceptance time in UTC (ADR-0007 change 2).</summary>
    public DateTimeOffset KnownAt { get; set; }

    public DateOnly FiledDate { get; set; }

    public DateOnly? PeriodOfReport { get; set; }

    public DateTimeOffset StoredAt { get; set; }

    public List<FilingFile> Files { get; } = [];
}

/// <summary>A stored file of a filing, with the SHA-256 it was stored with (lineage, NFR-DQ-01).</summary>
public sealed class FilingFile
{
    public required string AccessionNo { get; set; }

    public required string Name { get; set; }

    /// <summary>What the file is to the pipeline: primary, instance, schema or linkbase.</summary>
    public required string Kind { get; set; }

    public required string Sha256 { get; set; }

    public long Size { get; set; }
}

/// <summary>A job for the pipeline: the job contract and the pipeline's status (ADR-0002 rule 1).</summary>
public sealed class JobRun
{
    public long Id { get; set; }

    public required string AccessionNo { get; set; }

    /// <summary>The job contract as JSON (contracts/, ADR-0002 rule 2).</summary>
    public required string Contract { get; set; }

    public required string Status { get; set; }

    public string? Reason { get; set; }

    public int Attempts { get; set; }

    public DateTimeOffset CreatedAt { get; set; }

    public DateTimeOffset? StartedAt { get; set; }

    public DateTimeOffset? FinishedAt { get; set; }
}
