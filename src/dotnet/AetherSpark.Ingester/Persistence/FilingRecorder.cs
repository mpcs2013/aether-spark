using System.Text.Json;
using AetherSpark.Contracts;
using AetherSpark.Ingester.Sec;
using Microsoft.EntityFrameworkCore;
using Npgsql;

namespace AetherSpark.Ingester.Persistence;

/// <summary>What the ingester writes to the data Postgres (data-plane.md, sections 1 and 3).</summary>
public interface IFilingRecorder
{
    /// <summary>
    /// Of the given accession numbers, those already in `ref.filings`, each with the CIK it is stored
    /// under: the company's own or, for a joint filing, the company whose walk stored it first.
    /// </summary>
    Task<IReadOnlyDictionary<string, long>> StoredUnderAsync(IReadOnlyCollection<string> accessionNos,
        CancellationToken cancellationToken);

    /// <summary>That the company's index lists these stored filings (`ref.filing_companies`); rows already there stay.</summary>
    Task RecordListingsAsync(long cik, IReadOnlyCollection<string> accessionNos, CancellationToken cancellationToken);

    /// <summary>The company and today's tickers; a ticker no longer listed gets its end date.</summary>
    Task RecordIssuerAsync(long cik, string name, IReadOnlyList<ListedTicker> tickers, CancellationToken cancellationToken);

    /// <summary>
    /// The filing, its files, its company's listing and its job in one transaction; false when it is
    /// there already.
    /// </summary>
    Task<bool> RecordFilingAsync(long cik, SubmissionRow row, DateTimeOffset knownAt, FilingJob job,
        CancellationToken cancellationToken);
}

public sealed class FilingRecorder(IDbContextFactory<IngestDbContext> contexts, TimeProvider time) : IFilingRecorder
{
    public async Task<IReadOnlyDictionary<string, long>> StoredUnderAsync(IReadOnlyCollection<string> accessionNos,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(accessionNos);
        var wanted = accessionNos.Distinct(StringComparer.Ordinal).ToList();
        var db = await contexts.CreateDbContextAsync(cancellationToken).ConfigureAwait(false);
        await using (db.ConfigureAwait(false))
        {
            return await db.Filings.Where(f => wanted.Contains(f.AccessionNo))
                .ToDictionaryAsync(f => f.AccessionNo, f => f.Cik, StringComparer.Ordinal, cancellationToken)
                .ConfigureAwait(false);
        }
    }

    public async Task RecordListingsAsync(long cik, IReadOnlyCollection<string> accessionNos,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(accessionNos);
        var wanted = accessionNos.Distinct(StringComparer.Ordinal).ToList();
        if (wanted.Count == 0)
        {
            return;
        }

        var now = time.GetUtcNow();
        var db = await contexts.CreateDbContextAsync(cancellationToken).ConfigureAwait(false);
        await using (db.ConfigureAwait(false))
        {
            var listed = await db.FilingCompanies.Where(c => c.Cik == cik && wanted.Contains(c.AccessionNo))
                .Select(c => c.AccessionNo).ToHashSetAsync(StringComparer.Ordinal, cancellationToken).ConfigureAwait(false);
            db.FilingCompanies.AddRange(wanted.Where(a => !listed.Contains(a))
                .Select(a => new FilingCompany { AccessionNo = a, Cik = cik, FirstSeenAt = now }));
            try
            {
                await db.SaveChangesAsync(cancellationToken).ConfigureAwait(false);
            }
            catch (DbUpdateException e) when (e.InnerException is PostgresException { SqlState: PostgresErrorCodes.UniqueViolation })
            {
                // Another walk wrote one of these rows meanwhile; the next walk adds any still missing
            }
        }
    }

    public async Task RecordIssuerAsync(long cik, string name, IReadOnlyList<ListedTicker> tickers,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(tickers);
        var now = time.GetUtcNow();
        var today = DateOnly.FromDateTime(now.UtcDateTime);
        var db = await contexts.CreateDbContextAsync(cancellationToken).ConfigureAwait(false);
        await using (db.ConfigureAwait(false))
        {
            var issuer = await db.Issuers.FindAsync([cik], cancellationToken).ConfigureAwait(false);
            if (issuer is null)
            {
                db.Issuers.Add(new Issuer { Cik = cik, Name = name, FirstSeenAt = now, UpdatedAt = now });
            }
            else if (!string.Equals(issuer.Name, name, StringComparison.Ordinal))
            {
                issuer.Name = name;
                issuer.UpdatedAt = now;
            }

            // Validity as the ingester observes it: from the first walk that lists a ticker to the
            // first that no longer does (the SEC's index gives today's tickers only)
            var rows = await db.Tickers.Where(t => t.Cik == cik && (t.ValidTo == null || t.ValidFrom == today))
                .ToListAsync(cancellationToken).ConfigureAwait(false);
            foreach (var row in rows.Where(r => r.ValidTo is null && !tickers.Contains(new ListedTicker(r.Symbol, r.Exchange))))
            {
                row.ValidTo = today;
            }

            foreach (var ticker in tickers)
            {
                var same = rows.FirstOrDefault(r => r.Symbol == ticker.Symbol && r.Exchange == ticker.Exchange
                                                    && (r.ValidTo is null || r.ValidFrom == today));
                if (same is null)
                {
                    db.Tickers.Add(new Ticker { Cik = cik, Symbol = ticker.Symbol, Exchange = ticker.Exchange, ValidFrom = today });
                }
                else
                {
                    same.ValidTo = null;
                }
            }

            await db.SaveChangesAsync(cancellationToken).ConfigureAwait(false);
        }
    }

    public async Task<bool> RecordFilingAsync(long cik, SubmissionRow row, DateTimeOffset knownAt, FilingJob job,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(row);
        ArgumentNullException.ThrowIfNull(job);
        var now = time.GetUtcNow();
        var filing = new Filing
        {
            AccessionNo = row.AccessionNo,
            Cik = cik,
            Form = row.Form,
            KnownAt = knownAt.ToUniversalTime(),
            FiledDate = row.FilingDate,
            PeriodOfReport = row.ReportDate,
            StoredAt = now,
        };
        filing.Files.AddRange(job.Files.Select(f => new FilingFile
        {
            AccessionNo = row.AccessionNo,
            Name = f.Name,
            Kind = f.Kind,
            Sha256 = f.Sha256,
            Size = f.Size,
        }));

        var db = await contexts.CreateDbContextAsync(cancellationToken).ConfigureAwait(false);
        await using (db.ConfigureAwait(false))
        {
            db.Filings.Add(filing);
            db.FilingCompanies.Add(new FilingCompany { AccessionNo = row.AccessionNo, Cik = cik, FirstSeenAt = now });
            db.JobRuns.Add(new JobRun
            {
                AccessionNo = row.AccessionNo,
                Contract = JsonSerializer.Serialize(job),
                Status = "queued",
                CreatedAt = now,
            });
            try
            {
                // One SaveChanges is one transaction: the filing, its files, its listing and its job, or nothing
                await db.SaveChangesAsync(cancellationToken).ConfigureAwait(false);
                return true;
            }
            catch (DbUpdateException e) when (e.InnerException is PostgresException { SqlState: PostgresErrorCodes.UniqueViolation })
            {
                return false;
            }
        }
    }
}
