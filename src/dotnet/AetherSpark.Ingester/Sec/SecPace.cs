namespace AetherSpark.Ingester.Sec;

/// <summary>
/// The pace of all requests to the SEC, whatever the host (data-plane.md, section 3: at most 5 per
/// second; the SEC's limit of 10 counts per client). One instance for the process: the HTTP
/// handlers come and go with the client factory's rotation, the pace stays. Every attempt passes it,
/// retries included.
/// </summary>
public sealed class SecPace(TimeProvider time, TimeSpan spacing) : IDisposable
{
    private readonly SemaphoreSlim gate = new(1, 1);
    private long? lastStart;

    public async Task WaitTurnAsync(CancellationToken cancellationToken)
    {
        await gate.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            if (lastStart is { } last && time.GetElapsedTime(last) is var elapsed && elapsed < spacing)
            {
                await Task.Delay(spacing - elapsed, time, cancellationToken).ConfigureAwait(false);
            }

            lastStart = time.GetTimestamp();
        }
        finally
        {
            gate.Release();
        }
    }

    public void Dispose() => gate.Dispose();
}

/// <summary>Holds each request until the pace allows it; the innermost handler before the network.</summary>
public sealed class SecPacingHandler(SecPace pace) : DelegatingHandler
{
    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        await pace.WaitTurnAsync(cancellationToken).ConfigureAwait(false);
        return await base.SendAsync(request, cancellationToken).ConfigureAwait(false);
    }
}
