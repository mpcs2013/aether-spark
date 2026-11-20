using System.Diagnostics;
using System.Globalization;
using System.Net;
using System.Net.Sockets;
using System.Text;
using AetherSpark.Ingester.Sec;

namespace AetherSpark.Ingester.Tests;

/// <summary>A request as the loopback SEC received it: path, User-Agent and arrival time.</summary>
internal sealed record LoopbackRequest(string Path, string? UserAgent, TimeSpan At);

/// <summary>An answer of the loopback SEC.</summary>
internal sealed record LoopbackAnswer(int Status, byte[] Body, IReadOnlyDictionary<string, string>? Headers = null)
{
    public static LoopbackAnswer Ok(byte[] body) => new(200, body);

    public static LoopbackAnswer Ok(string body) => new(200, Encoding.UTF8.GetBytes(body));

    public static LoopbackAnswer Code(int status, IReadOnlyDictionary<string, string>? headers = null) =>
        new(status, Encoding.UTF8.GetBytes("refused"), headers);
}

/// <summary>
/// A stand-in SEC on 127.0.0.1 for the SEC client's tests (D4, D5 ingester side): plain HTTP/1.1,
/// one request per connection, answers from a function of the request and how often its path was
/// asked before. It records every request, so a test sees exactly what the ingester sent.
/// </summary>
internal sealed class LoopbackSec : IAsyncDisposable
{
    private readonly TcpListener listener = new(IPAddress.Loopback, 0);
    private readonly CancellationTokenSource stopping = new();
    private readonly Stopwatch clock = Stopwatch.StartNew();
    private readonly Lock gate = new();
    private readonly List<LoopbackRequest> requests = [];
    private readonly Func<LoopbackRequest, int, LoopbackAnswer> answer;
    private readonly Task accepting;

    public LoopbackSec(Func<LoopbackRequest, int, LoopbackAnswer> answer)
    {
        this.answer = answer;
        listener.Start();
        accepting = AcceptAsync();
    }

    public Uri Root => new($"http://127.0.0.1:{((IPEndPoint)listener.LocalEndpoint).Port}/");

    public IReadOnlyList<LoopbackRequest> Requests
    {
        get
        {
            lock (gate)
            {
                return [.. requests];
            }
        }
    }

    public async ValueTask DisposeAsync()
    {
        await stopping.CancelAsync().ConfigureAwait(false);
        listener.Stop();
        await accepting.ConfigureAwait(false);
        stopping.Dispose();
        listener.Dispose();
    }

    private async Task AcceptAsync()
    {
        while (!stopping.IsCancellationRequested)
        {
            TcpClient connection;
            try
            {
                connection = await listener.AcceptTcpClientAsync(stopping.Token).ConfigureAwait(false);
            }
            catch (Exception e) when (e is OperationCanceledException or SocketException or ObjectDisposedException)
            {
                return;
            }

            _ = ServeAsync(connection);
        }
    }

    private async Task ServeAsync(TcpClient connection)
    {
        using (connection)
        {
            var stream = connection.GetStream();
            var head = new StringBuilder();
            var buffer = new byte[4096];
            while (!head.ToString().Contains("\r\n\r\n", StringComparison.Ordinal) && head.Length < 65536)
            {
                var read = await stream.ReadAsync(buffer).ConfigureAwait(false);
                if (read == 0)
                {
                    return;
                }

                head.Append(Encoding.ASCII.GetString(buffer, 0, read));
            }

            var lines = head.ToString().Split("\r\n");
            var path = lines[0].Split(' ')[1];
            var agent = lines.Skip(1)
                .Where(l => l.StartsWith("User-Agent:", StringComparison.OrdinalIgnoreCase))
                .Select(l => l["User-Agent:".Length..].Trim())
                .FirstOrDefault();
            var request = new LoopbackRequest(path, agent, clock.Elapsed);
            int before;
            lock (gate)
            {
                before = requests.Count(r => r.Path == path);
                requests.Add(request);
            }

            LoopbackAnswer reply;
            try
            {
                reply = answer(request, before);
            }
#pragma warning disable CA1031 // A failing answer function becomes a 500 for the client to see
            catch (Exception)
#pragma warning restore CA1031
            {
                reply = LoopbackAnswer.Code(500);
            }

            var text = new StringBuilder()
                .Append(CultureInfo.InvariantCulture, $"HTTP/1.1 {reply.Status} Stub\r\n")
                .Append(CultureInfo.InvariantCulture, $"Content-Length: {reply.Body.Length}\r\n")
                .Append("Connection: close\r\n");
            foreach (var (name, value) in reply.Headers ?? new Dictionary<string, string>())
            {
                text.Append(CultureInfo.InvariantCulture, $"{name}: {value}\r\n");
            }

            text.Append("\r\n");
            await stream.WriteAsync(Encoding.ASCII.GetBytes(text.ToString())).ConfigureAwait(false);
            await stream.WriteAsync(reply.Body).ConfigureAwait(false);
        }
    }
}

/// <summary>Settings and a client for tests against the loopback SEC.</summary>
internal static class SecTesting
{
    /// <summary>A dummy contact, as in CI; it must never appear in a log or a trace.</summary>
    public const string Contact = "stack-test@example.org";

    public static IngesterSettings Settings(Uri sec, string raw, TimeSpan? spacing = null) => new()
    {
        DatabaseHost = "unused.invalid",
        SecArchives = sec,
        SecData = sec,
        RawDirectory = raw,
        RequestSpacing = spacing ?? TimeSpan.FromMilliseconds(210),
        RetryDelay = TimeSpan.FromMilliseconds(10),
    };

    public static SecClient Client(IngesterSettings settings, SecStop stop, SecPace pace) =>
        new(SecHttp.CreateClient(settings, new SecIdentity(Contact), pace, SecHttp.PrimaryHandler(settings)), settings, stop);

    public static string TempFolder()
    {
        var path = Path.Combine(Path.GetTempPath(), "aetherspark-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(path);
        return path;
    }
}
