using Polly.Timeout;

namespace AetherSpark.Ingester.Sec;

/// <summary>
/// Requests to the SEC (data-plane.md, section 3), only to the URLs SecUrls builds, over the client
/// SecHttp builds. Each answer is read with a size limit; a failure after the retries counts toward
/// the stop (SecStop).
/// </summary>
public sealed class SecClient(HttpClient client, IngesterSettings settings, SecStop stop) : IDisposable
{
    public void Dispose() => client.Dispose();

    // Reading a body is outside the resilience pipeline (it ends with the headers)
    private static readonly TimeSpan BodyTimeout = TimeSpan.FromMinutes(10);

    public Task<byte[]> SubmissionsAsync(long cik, CancellationToken cancellationToken) =>
        BytesAsync(SecUrls.Submissions(settings.SecData, cik), Submissions.MaxBytes, cancellationToken);

    public Task<byte[]> SubmissionsPageAsync(long cik, string page, CancellationToken cancellationToken) =>
        BytesAsync(SecUrls.SubmissionsPage(settings.SecData, cik, page), Submissions.MaxBytes, cancellationToken);

    public Task<byte[]> FilingIndexAsync(long cik, string accession, CancellationToken cancellationToken) =>
        BytesAsync(SecUrls.FilingIndex(settings.SecArchives, cik, accession), FileChoice.MaxIndexBytes, cancellationToken);

    public Task<byte[]> HeaderAsync(long cik, string accession, CancellationToken cancellationToken) =>
        BytesAsync(SecUrls.Header(settings.SecArchives, cik, accession), FilingHeader.MaxBytes, cancellationToken);

    /// <summary>Fetches one file straight into the raw folder, hashed on the way.</summary>
    public async Task<StoredFile> StoreAsync(long cik, string accession, ChosenFile file, RawStore raw,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(file);
        ArgumentNullException.ThrowIfNull(raw);
        var url = SecUrls.File(settings.SecArchives, cik, accession, file.Name);
        using var response = await SendAsync(url, cancellationToken).ConfigureAwait(false);
        return await ReadBodyAsync(url, async token =>
        {
            var body = await response.Content.ReadAsStreamAsync(token).ConfigureAwait(false);
            await using (body.ConfigureAwait(false))
            {
                return await raw.WriteAsync(cik, accession, file, body, token).ConfigureAwait(false);
            }
        }, cancellationToken).ConfigureAwait(false);
    }

    private async Task<byte[]> BytesAsync(Uri url, int maxBytes, CancellationToken cancellationToken)
    {
        using var response = await SendAsync(url, cancellationToken).ConfigureAwait(false);
        if (response.Content.Headers.ContentLength > maxBytes)
        {
            throw new FormatException($"the answer for {url.AbsolutePath} is larger than {maxBytes} bytes");
        }

        return await ReadBodyAsync(url, async token =>
        {
            var body = await response.Content.ReadAsStreamAsync(token).ConfigureAwait(false);
            await using (body.ConfigureAwait(false))
            {
                using var copy = new MemoryStream();
                var buffer = new byte[81920];
                int read;
                while ((read = await body.ReadAsync(buffer, token).ConfigureAwait(false)) > 0)
                {
                    if (copy.Length + read > maxBytes)
                    {
                        throw new FormatException($"the answer for {url.AbsolutePath} is larger than {maxBytes} bytes");
                    }

                    copy.Write(buffer, 0, read);
                }

                return copy.ToArray();
            }
        }, cancellationToken).ConfigureAwait(false);
    }

    private async Task<HttpResponseMessage> SendAsync(Uri url, CancellationToken cancellationToken)
    {
        stop.ThrowIfStopped();
        HttpResponseMessage response;
        try
        {
            response = await client.GetAsync(url, HttpCompletionOption.ResponseHeadersRead, cancellationToken)
                .ConfigureAwait(false);
        }
        catch (Exception e) when (e is HttpRequestException or TimeoutRejectedException
                                      || (e is OperationCanceledException && !cancellationToken.IsCancellationRequested))
        {
            stop.Failed(0);
            throw new SecRequestException(url, 0, e);
        }

        if (response.IsSuccessStatusCode)
        {
            stop.Succeeded();
            return response;
        }

        var status = (int)response.StatusCode;
        response.Dispose();
        stop.Failed(status);
        throw new SecRequestException(url, status);
    }

    private async Task<T> ReadBodyAsync<T>(Uri url, Func<CancellationToken, Task<T>> read, CancellationToken cancellationToken)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(BodyTimeout);
        try
        {
            return await read(timeout.Token).ConfigureAwait(false);
        }
        catch (Exception e) when (e is IOException or HttpRequestException
                                      || (e is OperationCanceledException && !cancellationToken.IsCancellationRequested))
        {
            stop.Failed(0);
            throw new SecRequestException(url, 0, e);
        }
    }
}
