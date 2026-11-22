namespace AetherSpark.Ingester.Sec;

/// <summary>
/// The stop after repeated refusals (data-plane.md, section 3): a 403 stops at once (the SEC refuses
/// this client, typically its User-Agent), and three requests in a row that failed after their
/// retries (429, 5xx, no answer) stop too. Once stopped, no request leaves until the next walk.
/// A 404 is the filing's problem, not a refusal, and does not count.
/// </summary>
public sealed class SecStop
{
    public const int Limit = 3;

    private readonly Lock gate = new();
    private int failures;
    private string? reason;

    public bool IsStopped
    {
        get
        {
            lock (gate)
            {
                return reason is not null;
            }
        }
    }

    public void ThrowIfStopped()
    {
        lock (gate)
        {
            if (reason is not null)
            {
                throw new SecStoppedException(reason);
            }
        }
    }

    public void Succeeded()
    {
        lock (gate)
        {
            failures = 0;
        }
    }

    /// <summary>A failed request after its retries; `status` 0 means no answer.</summary>
    public void Failed(int status)
    {
        lock (gate)
        {
            if (status == 403)
            {
                reason ??= "the SEC answered 403: it refuses this client (check the User-Agent contact)";
            }
            else if (status != 404 && ++failures >= Limit)
            {
                reason ??= $"{Limit} requests in a row failed after their retries (last: {(status == 0 ? "no answer" : status)})";
            }
        }
    }

    /// <summary>A new walk starts with a clean slate.</summary>
    public void Reset()
    {
        lock (gate)
        {
            failures = 0;
            reason = null;
        }
    }
}

/// <summary>No more requests until the next walk.</summary>
public sealed class SecStoppedException : Exception
{
    public SecStoppedException()
    {
    }

    public SecStoppedException(string message)
        : base(message)
    {
    }

    public SecStoppedException(string message, Exception innerException)
        : base(message, innerException)
    {
    }
}

/// <summary>One request failed after its retries; the URL is the SEC's public path.</summary>
public sealed class SecRequestException : Exception
{
    public SecRequestException()
    {
    }

    public SecRequestException(string message)
        : base(message)
    {
    }

    public SecRequestException(string message, Exception innerException)
        : base(message, innerException)
    {
    }

    /// <param name="server">The answer's Server header, when it gave one: inside the TLS tunnel only the
    /// SEC's side can answer (the proxy's own failures come as no answer), so this tells which of its
    /// servers did.</param>
    public SecRequestException(Uri url, int status, Exception? inner = null, string? server = null)
        : base($"{(status == 0 ? "no answer" : $"HTTP {status}")} for {url?.AbsolutePath}{Server(server)}", inner)
    {
        Status = status;
    }

    // At most 60 printable ASCII characters of the header, so an answer cannot write into the log
    private static string Server(string? server)
    {
        if (string.IsNullOrWhiteSpace(server))
        {
            return "";
        }

        var plain = new string(server.Where(c => c is >= ' ' and <= '~').Take(60).ToArray()).Trim();
        return plain.Length == 0 ? "" : $" (server: {plain})";
    }

    /// <summary>The HTTP status, or 0 when no answer came.</summary>
    public int Status { get; }
}
