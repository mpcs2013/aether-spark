using AetherSpark.Contracts;
using AetherSpark.Ingester.Persistence;
using AetherSpark.Ingester.Sec;
using Microsoft.Extensions.Logging;

namespace AetherSpark.Ingester.Walk;

/// <summary>
/// What one company's fetch did, for the log and the tests. <paramref name="Shared"/>: filings its
/// index lists that are stored under another company (a joint filing); <paramref name="CompanyRefused"/>:
/// the company itself was refused (its index unreadable or naming another CIK).
/// </summary>
public sealed record CompanyResult(int HandedOff, int Known, int Refused, bool Stopped, int Shared = 0,
    bool CompanyRefused = false);

/// <summary>
/// One company (data-plane.md, section 3): its submissions index (with the older pages that reach
/// the period), its name and tickers, then each 10-K, 10-Q and amendment filed since FiledFrom and
/// not yet in `ref.filings`, oldest first: the header, `known_at` checked against the index (D3),
/// the folder index, the chosen files stored and hashed, and the filing with its job in one
/// transaction. A filing already stored under another company (a joint filing) costs no request:
/// the company's listing is recorded and a copy left in its own folder is removed (owner,
/// 2026-11-22). A filing that fails a check is refused with its reason and the next one goes on;
/// the SEC's stop ends the company. Logs carry the CIK, accession numbers and reasons, never file
/// content or the User-Agent.
/// </summary>
public sealed partial class CompanyFetcher(
    SecClient sec,
    RawStore raw,
    IFilingRecorder recorder,
    IngesterSettings settings,
    WalkProgress progress,
    ILogger<CompanyFetcher> logger)
{
    /// <summary>One company of a walk; the walk's end is logged after its last company.</summary>
    public async Task FetchForWalkAsync(FetchCompany message, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(message);
        progress.Starting(message);
        var result = await FetchAsync(message.Cik, cancellationToken).ConfigureAwait(false);
        progress.Finished(message, result);
    }

    public async Task<CompanyResult> FetchAsync(long cik, CancellationToken cancellationToken)
    {
        int handed = 0, refused = 0, known = 0, shared = 0;
        try
        {
            var submissions = Submissions.Parse(await sec.SubmissionsAsync(cik, cancellationToken).ConfigureAwait(false));
            if (submissions.Cik != cik)
            {
                LogCompanyRefused(logger, cik, "the index names another CIK");
                return new CompanyResult(0, 0, 0, false, CompanyRefused: true);
            }

            var rows = new List<SubmissionRow>(submissions.Filings);
            var leftOut = submissions.RowsLeftOut;
            foreach (var page in submissions.Pages.Where(p => p.FilingTo >= settings.FiledFrom))
            {
                var (pageRows, pageLeftOut) = Submissions.ParsePage(
                    await sec.SubmissionsPageAsync(cik, page.Name, cancellationToken).ConfigureAwait(false));
                rows.AddRange(pageRows);
                leftOut += pageLeftOut;
            }

            if (leftOut > 0)
            {
                LogRowsLeftOut(logger, cik, leftOut);
            }

            await recorder.RecordIssuerAsync(cik, submissions.Name, submissions.Tickers, cancellationToken).ConfigureAwait(false);
            var wanted = rows
                .Where(r => SecNames.Forms.Contains(r.Form) && r.FilingDate >= settings.FiledFrom)
                .DistinctBy(r => r.AccessionNo, StringComparer.Ordinal)
                .OrderBy(r => r.FilingDate).ThenBy(r => r.AccessionNo, StringComparer.Ordinal)
                .ToList();

            // Stored under any company: a joint filing is stored once, under the company walked first
            var stored = await recorder.StoredUnderAsync(wanted.Select(r => r.AccessionNo).ToList(), cancellationToken)
                .ConfigureAwait(false);
            known = stored.Values.Count(owner => owner == cik);
            shared = stored.Count - known;
            await recorder.RecordListingsAsync(cik, stored.Keys.ToList(), cancellationToken).ConfigureAwait(false);
            foreach (var (accession, owner) in stored.Where(s => s.Value != cik).OrderBy(s => s.Key, StringComparer.Ordinal))
            {
                RemoveCopy(cik, accession, owner);
            }

            foreach (var row in wanted.Where(r => !stored.ContainsKey(r.AccessionNo)))
            {
                var refusal = await FetchFilingAsync(cik, row, cancellationToken).ConfigureAwait(false);
                if (refusal is null)
                {
                    handed++;
                }
                else
                {
                    refused++;
                    LogFilingRefused(logger, cik, row.AccessionNo, refusal);
                }
            }

            LogCompany(logger, cik, handed, known, shared, refused);
            return new CompanyResult(handed, known, refused, false, shared);
        }
        catch (SecStoppedException e)
        {
            LogStopped(logger, cik, e.Message, handed);
            return new CompanyResult(handed, known, refused, true, shared);
        }
        catch (Exception e) when (e is SecRequestException or FormatException or System.Text.Json.JsonException)
        {
            LogCompanyRefused(logger, cik, e.Message);
            return new CompanyResult(handed, known, refused, false, shared, CompanyRefused: true);
        }
    }

    // A joint filing stored under another company: the pipeline reads it from that company's folder,
    // so a folder under this company's CIK is a copy an earlier walk left (before 2026-11-22 every
    // walk fetched the filing again and the database refused its record). Never lets a failed
    // removal end the company: the next walk tries again.
    private void RemoveCopy(long cik, string accessionNo, long owner)
    {
        try
        {
            if (raw.RemoveFiling(cik, accessionNo))
            {
                LogCopyRemoved(logger, cik, accessionNo, owner);
            }
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            LogFilesNotRemoved(logger, cik, accessionNo, e.Message);
        }
    }

    // Null when handed off; otherwise the reason it was refused. The stop passes through. A filing that
    // ends before its record is written (refused, stopped, cancelled) leaves no files behind; from the
    // record's write on they stay, since a write whose answer was lost may still have committed, and
    // a filing another walk stored first keeps the files that walk wrote.
    private async Task<string?> FetchFilingAsync(long cik, SubmissionRow row, CancellationToken cancellationToken)
    {
        var mayBeRecorded = false;
        try
        {
            var header = FilingHeader.Parse(
                await sec.HeaderAsync(cik, row.AccessionNo, cancellationToken).ConfigureAwait(false));
            if (header.AccessionNo != row.AccessionNo || header.Type != row.Form || header.FilingDate != row.FilingDate)
            {
                return "the header disagrees with the index (accession number, form or filing date)";
            }

            var knownAt = AcceptanceTime.FromHeader(header.AcceptanceDateTime);
            var indexTime = AcceptanceTime.FromIndex(row.AcceptanceDateTime);
            if (knownAt != indexTime)
            {
                return $"acceptance times differ: header {AcceptanceTime.ToContract(knownAt)}, index {AcceptanceTime.ToContract(indexTime)}";
            }

            var names = FileChoice.ParseIndex(
                await sec.FilingIndexAsync(cik, row.AccessionNo, cancellationToken).ConfigureAwait(false));
            var (chosen, refusal) = FileChoice.Choose(names, row);
            if (refusal is not null)
            {
                return refusal;
            }

            var files = new List<JobFile>();
            foreach (var file in chosen)
            {
                var stored = await sec.StoreAsync(cik, row.AccessionNo, file, raw, cancellationToken).ConfigureAwait(false);
                files.Add(new JobFile(stored.Name, stored.Kind, stored.Sha256, checked((int)stored.Size)));
            }

            var job = new FilingJob(1, row.AccessionNo, cik, row.Form, AcceptanceTime.ToContract(knownAt),
                row.FilingDate.ToString("yyyy-MM-dd", System.Globalization.CultureInfo.InvariantCulture), files);
            var problems = job.Validate();
            if (problems.Count > 0)
            {
                return "the job contract does not hold: " + string.Join("; ", problems);
            }

            mayBeRecorded = true;
            return await recorder.RecordFilingAsync(cik, row, knownAt, job, cancellationToken).ConfigureAwait(false)
                ? null
                : "already recorded (stored meanwhile by another company's walk or a second walk)";
        }
        catch (Exception e) when (e is SecRequestException or FormatException or FileTooLargeException
                                      or System.Text.Json.JsonException)
        {
            return e.Message;
        }
        finally
        {
            if (!mayBeRecorded)
            {
                await RemoveUnrecordedAsync(cik, row.AccessionNo).ConfigureAwait(false);
            }
        }
    }

    // Asks the database first (another walk may have stored the filing meanwhile; also after a
    // cancellation, so without the walk's token) and removes nothing when it cannot tell. The folder
    // stays only when the filing is stored under this company. Never lets a failed removal hide why
    // the filing ended: the next walk overwrites what is left.
    private async Task RemoveUnrecordedAsync(long cik, string accessionNo)
    {
        if (!Directory.Exists(raw.FolderFor(cik, accessionNo)))
        {
            return;
        }

        try
        {
            var recorded = await recorder.StoredUnderAsync([accessionNo], CancellationToken.None).ConfigureAwait(false);
            var ours = recorded.TryGetValue(accessionNo, out var owner) && owner == cik;
            if (!ours && raw.RemoveFiling(cik, accessionNo))
            {
                LogFilesRemoved(logger, cik, accessionNo);
            }
        }
        catch (Exception e) when (e is not OutOfMemoryException)
        {
            LogFilesNotRemoved(logger, cik, accessionNo, e.Message);
        }
    }

    [LoggerMessage(Level = LogLevel.Information, Message = "company {Cik}, filing {AccessionNo}: its stored files removed (no record)")]
    private static partial void LogFilesRemoved(ILogger logger, long cik, string accessionNo);

    [LoggerMessage(Level = LogLevel.Warning, Message = "company {Cik}, filing {AccessionNo}: its stored files could not be removed: {Reason}")]
    private static partial void LogFilesNotRemoved(ILogger logger, long cik, string accessionNo, string reason);

    [LoggerMessage(Level = LogLevel.Information,
        Message = "company {Cik}, filing {AccessionNo}: its copy removed (the filing is stored under company {Owner})")]
    private static partial void LogCopyRemoved(ILogger logger, long cik, string accessionNo, long owner);

    [LoggerMessage(Level = LogLevel.Information,
        Message = "company {Cik}: {HandedOff} filings handed off, {Known} already stored, {Shared} stored under another company, {Refused} refused")]
    private static partial void LogCompany(ILogger logger, long cik, int handedOff, int known, int shared, int refused);

    [LoggerMessage(Level = LogLevel.Warning, Message = "company {Cik}, filing {AccessionNo} refused: {Reason}")]
    private static partial void LogFilingRefused(ILogger logger, long cik, string accessionNo, string reason);

    [LoggerMessage(Level = LogLevel.Warning, Message = "company {Cik} refused: {Reason}")]
    private static partial void LogCompanyRefused(ILogger logger, long cik, string reason);

    [LoggerMessage(Level = LogLevel.Warning, Message = "company {Cik}: {LeftOut} rows of the SEC's index left out (malformed)")]
    private static partial void LogRowsLeftOut(ILogger logger, long cik, int leftOut);

    [LoggerMessage(Level = LogLevel.Error,
        Message = "company {Cik}: stopped after {HandedOff} filings, no more requests until the next walk: {Reason}")]
    private static partial void LogStopped(ILogger logger, long cik, string reason, int handedOff);
}
