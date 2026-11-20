using AetherSpark.Contracts;
using AetherSpark.Ingester.Persistence;
using AetherSpark.Ingester.Sec;
using Microsoft.Extensions.Logging;

namespace AetherSpark.Ingester.Walk;

/// <summary>What one company's fetch did, for the log and the tests.</summary>
public sealed record CompanyResult(int HandedOff, int Known, int Refused, bool Stopped);

/// <summary>
/// One company (data-plane.md, section 3): its submissions index (with the older pages that reach
/// the period), its name and tickers, then each 10-K, 10-Q and amendment filed since FiledFrom and
/// not yet in `ref.filings`, oldest first: the header, `known_at` checked against the index (D3),
/// the folder index, the chosen files stored and hashed, and the filing with its job in one
/// transaction. A filing that fails a check is refused with its reason and the next one goes on;
/// the SEC's stop ends the company. Logs carry the CIK, accession numbers and reasons, never file
/// content or the User-Agent.
/// </summary>
public sealed partial class CompanyFetcher(
    SecClient sec,
    RawStore raw,
    IFilingRecorder recorder,
    IngesterSettings settings,
    ILogger<CompanyFetcher> logger)
{
    public async Task<CompanyResult> FetchAsync(long cik, CancellationToken cancellationToken)
    {
        int handed = 0, refused = 0, known = 0;
        try
        {
            var submissions = Submissions.Parse(await sec.SubmissionsAsync(cik, cancellationToken).ConfigureAwait(false));
            if (submissions.Cik != cik)
            {
                LogCompanyRefused(logger, cik, "the index names another CIK");
                return new CompanyResult(0, 0, 0, false);
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
            var stored = await recorder.KnownAccessionsAsync(cik, cancellationToken).ConfigureAwait(false);
            var wanted = rows
                .Where(r => SecNames.Forms.Contains(r.Form) && r.FilingDate >= settings.FiledFrom)
                .DistinctBy(r => r.AccessionNo, StringComparer.Ordinal)
                .OrderBy(r => r.FilingDate).ThenBy(r => r.AccessionNo, StringComparer.Ordinal)
                .ToList();
            known = wanted.Count(r => stored.Contains(r.AccessionNo));
            foreach (var row in wanted.Where(r => !stored.Contains(r.AccessionNo)))
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

            LogCompany(logger, cik, handed, known, refused);
            return new CompanyResult(handed, known, refused, false);
        }
        catch (SecStoppedException e)
        {
            LogStopped(logger, cik, e.Message, handed);
            return new CompanyResult(handed, known, refused, true);
        }
        catch (Exception e) when (e is SecRequestException or FormatException or System.Text.Json.JsonException)
        {
            LogCompanyRefused(logger, cik, e.Message);
            return new CompanyResult(handed, known, refused, false);
        }
    }

    // Null when handed off; otherwise the reason it was refused. The stop passes through.
    private async Task<string?> FetchFilingAsync(long cik, SubmissionRow row, CancellationToken cancellationToken)
    {
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

            return await recorder.RecordFilingAsync(cik, row, knownAt, job, cancellationToken).ConfigureAwait(false)
                ? null
                : "already recorded (another walk stored it first)";
        }
        catch (Exception e) when (e is SecRequestException or FormatException or FileTooLargeException
                                      or System.Text.Json.JsonException)
        {
            return e.Message;
        }
    }

    [LoggerMessage(Level = LogLevel.Information,
        Message = "company {Cik}: {HandedOff} filings handed off, {Known} already stored, {Refused} refused")]
    private static partial void LogCompany(ILogger logger, long cik, int handedOff, int known, int refused);

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
