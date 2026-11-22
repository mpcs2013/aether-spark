using System.Diagnostics;
using AetherSpark.Ingester.Sec;

namespace AetherSpark.Ingester.Tests;

/// <summary>
/// D4 (data-plane.md, section 6): against a stand-in SEC, never more than 5 requests per second, the
/// User-Agent on every request and in no trace, backoff on 429 and 503, a stop after repeated
/// refusals; and no redirect followed (D5).
/// </summary>
public sealed class SecClientTests
{
    private const long Cik = 320193;
    private const string Accession = "0000320193-24-000081";

    private static CancellationToken Token => TestContext.Current.CancellationToken;

    [Fact]
    public async Task NeverMoreThanFiveRequestsInAnySecondEvenFromParallelCallers()
    {
        // The ingester's own spacing, measured where the SEC counts: on arrival, after each new
        // connection's set-up (on a cold CI runner that alone squeezed 210 ms spacing below a second).
        await using var sec = new LoopbackSec((_, _) => LoopbackAnswer.Ok("{}"));
        var settings = SecTesting.Settings(sec.Root, SecTesting.TempFolder());
        using var pace = new SecPace(TimeProvider.System, settings.RequestSpacing);
        using var client = SecTesting.Client(settings, new SecStop(), pace);

        await Task.WhenAll(Enumerable.Range(0, 12).Select(_ => client.HeaderAsync(Cik, Accession, Token)));

        var arrivals = sec.Requests.Select(r => r.At).Order().ToList();
        Assert.Equal(12, arrivals.Count);
        for (var i = 0; i + 5 < arrivals.Count; i++)
        {
            Assert.True(arrivals[i + 5] - arrivals[i] >= TimeSpan.FromSeconds(1),
                $"requests {i} to {i + 5} arrived within {arrivals[i + 5] - arrivals[i]}");
        }
    }

    [Fact]
    public async Task EveryRequestCarriesTheUserAgentAndNoTraceDoes()
    {
        var traced = new List<string>();
        using var listener = new ActivityListener
        {
            ShouldListenTo = _ => true,
            Sample = (ref ActivityCreationOptions<ActivityContext> _) => ActivitySamplingResult.AllDataAndRecorded,
            ActivityStopped = a =>
            {
                lock (traced)
                {
                    traced.Add(a.DisplayName);
                    traced.AddRange(a.TagObjects.Select(t => $"{t.Key}={t.Value}"));
                    traced.AddRange(a.Events.Select(e => e.Name));
                    traced.AddRange(a.Baggage.Select(b => $"{b.Key}={b.Value}"));
                }
            },
        };
        ActivitySource.AddActivityListener(listener);
        await using var sec = new LoopbackSec((_, _) => LoopbackAnswer.Ok("{}"));
        using var pace = new SecPace(TimeProvider.System, TimeSpan.FromMilliseconds(10));
        using var client = SecTesting.Client(SecTesting.Settings(sec.Root, SecTesting.TempFolder()), new SecStop(), pace);

        await client.HeaderAsync(Cik, Accession, Token);
        await client.FilingIndexAsync(Cik, Accession, Token);

        Assert.All(sec.Requests, r => Assert.Equal("AetherSpark " + SecTesting.Contact, r.UserAgent));
        lock (traced)
        {
            Assert.NotEmpty(traced);
            Assert.DoesNotContain(traced, t => t.Contains(SecTesting.Contact, StringComparison.OrdinalIgnoreCase));
        }
    }

    [Fact]
    public async Task BacksOffOn503And429ThenSucceeds()
    {
        await using var sec = new LoopbackSec((_, n) => n switch
        {
            0 => LoopbackAnswer.Code(503),
            1 => LoopbackAnswer.Code(429),
            _ => LoopbackAnswer.Ok("{}"),
        });
        using var pace = new SecPace(TimeProvider.System, TimeSpan.FromMilliseconds(10));
        var stop = new SecStop();
        using var client = SecTesting.Client(SecTesting.Settings(sec.Root, SecTesting.TempFolder()), stop, pace);

        var body = await client.HeaderAsync(Cik, Accession, Token);

        Assert.Equal("{}"u8.ToArray(), body);
        Assert.Equal(3, sec.Requests.Count);
        Assert.False(stop.IsStopped);
    }

    [Fact]
    public async Task HonoursRetryAfter()
    {
        await using var sec = new LoopbackSec((_, n) => n == 0
            ? LoopbackAnswer.Code(429, new Dictionary<string, string> { ["Retry-After"] = "1" })
            : LoopbackAnswer.Ok("{}"));
        using var pace = new SecPace(TimeProvider.System, TimeSpan.FromMilliseconds(10));
        using var client = SecTesting.Client(SecTesting.Settings(sec.Root, SecTesting.TempFolder()), new SecStop(), pace);

        await client.HeaderAsync(Cik, Accession, Token);

        var arrivals = sec.Requests.Select(r => r.At).ToList();
        Assert.Equal(2, arrivals.Count);
        Assert.True(arrivals[1] - arrivals[0] >= TimeSpan.FromSeconds(0.95), $"retried after {arrivals[1] - arrivals[0]}");
    }

    [Fact]
    public async Task StopsAfterThreeRequestsFailedTheirRetriesAndSendsNothingMore()
    {
        await using var sec = new LoopbackSec((_, _) => LoopbackAnswer.Code(503));
        using var pace = new SecPace(TimeProvider.System, TimeSpan.FromMilliseconds(10));
        var stop = new SecStop();
        using var client = SecTesting.Client(SecTesting.Settings(sec.Root, SecTesting.TempFolder()), stop, pace);

        for (var i = 0; i < SecStop.Limit; i++)
        {
            var failed = await Assert.ThrowsAsync<SecRequestException>(() => client.HeaderAsync(Cik, Accession, Token));
            Assert.Equal(503, failed.Status);
        }

        // One attempt and four retries each: ServiceDefaults' standard handler is not added on top
        Assert.Equal(SecStop.Limit * (1 + SecHttp.MaxRetries), sec.Requests.Count);
        Assert.True(stop.IsStopped);
        await Assert.ThrowsAsync<SecStoppedException>(() => client.HeaderAsync(Cik, Accession, Token));
        Assert.Equal(SecStop.Limit * (1 + SecHttp.MaxRetries), sec.Requests.Count);
    }

    [Fact]
    public async Task AFailedAnswerNamesTheServerThatGaveIt()
    {
        var headers = new Dictionary<string, string> { ["Server"] = "AkamaiGHost" };
        await using var sec = new LoopbackSec((_, _) => LoopbackAnswer.Code(502, headers));
        using var pace = new SecPace(TimeProvider.System, TimeSpan.FromMilliseconds(10));
        using var client = SecTesting.Client(SecTesting.Settings(sec.Root, SecTesting.TempFolder()), new SecStop(), pace);

        var failed = await Assert.ThrowsAsync<SecRequestException>(() => client.HeaderAsync(Cik, Accession, Token));

        Assert.Equal(502, failed.Status);
        Assert.EndsWith(".hdr.sgml (server: AkamaiGHost)", failed.Message, StringComparison.Ordinal);
    }

    [Theory]
    [InlineData(null, "HTTP 504 for /x")]
    [InlineData("  ", "HTTP 504 for /x")]
    [InlineData("nginx", "HTTP 504 for /x (server: nginx)")]
    [InlineData("evil\u001b[31m\u2028end", "HTTP 504 for /x (server: evil[31mend)")]
    public void TheServerNameIsPlainAsciiAndShort(string? server, string expected)
    {
        Assert.Equal(expected, new SecRequestException(new Uri("https://www.sec.gov/x"), 504, server: server).Message);
        var longName = new SecRequestException(new Uri("https://www.sec.gov/x"), 504, server: new string('a', 500)).Message;
        Assert.EndsWith($"(server: {new string('a', 60)})", longName, StringComparison.Ordinal);
    }

    [Fact]
    public async Task AProxyThatRefusesTheTunnelIsNoAnswerNotTheSecsStatus()
    {
        // The loopback answers the CONNECT itself with 502, as a proxy that cannot reach the SEC does
        await using var proxy = new LoopbackSec((_, _) => LoopbackAnswer.Code(502));
        using var pace = new SecPace(TimeProvider.System, TimeSpan.FromMilliseconds(10));
        var settings = SecTesting.Settings(new Uri("https://www.sec.gov/"), SecTesting.TempFolder()) with { Proxy = proxy.Root };
        using var client = SecTesting.Client(settings, new SecStop(), pace);

        var failed = await Assert.ThrowsAsync<SecRequestException>(() => client.HeaderAsync(Cik, Accession, Token));

        Assert.Equal(0, failed.Status);
        Assert.StartsWith("no answer for ", failed.Message, StringComparison.Ordinal);
        Assert.All(proxy.Requests, r => Assert.Equal("www.sec.gov:443", r.Path));
    }

    [Fact]
    public async Task A403StopsAtOnceWithoutRetry()
    {
        await using var sec = new LoopbackSec((_, _) => LoopbackAnswer.Code(403));
        using var pace = new SecPace(TimeProvider.System, TimeSpan.FromMilliseconds(10));
        var stop = new SecStop();
        using var client = SecTesting.Client(SecTesting.Settings(sec.Root, SecTesting.TempFolder()), stop, pace);

        await Assert.ThrowsAsync<SecRequestException>(() => client.HeaderAsync(Cik, Accession, Token));
        await Assert.ThrowsAsync<SecStoppedException>(() => client.FilingIndexAsync(Cik, Accession, Token));

        Assert.Single(sec.Requests);
        stop.Reset();
        Assert.False(stop.IsStopped);
    }

    [Fact]
    public async Task A404IsNotARefusal()
    {
        await using var sec = new LoopbackSec((_, _) => LoopbackAnswer.Code(404));
        using var pace = new SecPace(TimeProvider.System, TimeSpan.FromMilliseconds(10));
        var stop = new SecStop();
        using var client = SecTesting.Client(SecTesting.Settings(sec.Root, SecTesting.TempFolder()), stop, pace);

        for (var i = 0; i < 5; i++)
        {
            await Assert.ThrowsAsync<SecRequestException>(() => client.HeaderAsync(Cik, Accession, Token));
        }

        Assert.Equal(5, sec.Requests.Count);
        Assert.False(stop.IsStopped);
    }

    [Fact]
    public async Task ARedirectIsNotFollowed()
    {
        await using var sec = new LoopbackSec((r, _) => r.Path.StartsWith("/elsewhere", StringComparison.Ordinal)
            ? LoopbackAnswer.Ok("{}")
            : LoopbackAnswer.Code(302, new Dictionary<string, string> { ["Location"] = "/elsewhere/secret" }));
        using var pace = new SecPace(TimeProvider.System, TimeSpan.FromMilliseconds(10));
        using var client = SecTesting.Client(SecTesting.Settings(sec.Root, SecTesting.TempFolder()), new SecStop(), pace);

        var failed = await Assert.ThrowsAsync<SecRequestException>(() => client.HeaderAsync(Cik, Accession, Token));

        Assert.Equal(302, failed.Status);
        Assert.DoesNotContain(sec.Requests, r => r.Path.StartsWith("/elsewhere", StringComparison.Ordinal));
    }

    [Fact]
    public async Task AnAnswerAboveItsLimitIsRefused()
    {
        await using var sec = new LoopbackSec((_, _) => LoopbackAnswer.Ok(new byte[FilingHeader.MaxBytes + 1]));
        using var pace = new SecPace(TimeProvider.System, TimeSpan.FromMilliseconds(10));
        using var client = SecTesting.Client(SecTesting.Settings(sec.Root, SecTesting.TempFolder()), new SecStop(), pace);

        await Assert.ThrowsAsync<FormatException>(() => client.HeaderAsync(Cik, Accession, Token));
    }

    [Fact]
    public void TheNetworkHandlerFollowsNoRedirectAndUsesOnlyTheConfiguredProxy()
    {
        var direct = SecTesting.Settings(new Uri("https://www.sec.gov/"), "/raw");
        using var none = SecHttp.PrimaryHandler(direct);
        Assert.False(none.AllowAutoRedirect);
        Assert.False(none.UseProxy);
        Assert.False(none.UseCookies);

        var proxy = new Uri("http://172.30.10.2:3128");
        using var via = SecHttp.PrimaryHandler(direct with { Proxy = proxy });
        Assert.True(via.UseProxy);
        Assert.Equal(proxy, via.Proxy!.GetProxy(new Uri("https://www.sec.gov/Archives/")));
    }

    [Theory]
    [InlineData("someone@example.org", true)]
    [InlineData("someone@example.org\r\nX-Injected: 1", false)]
    [InlineData("someone@example.org\n", false)]
    [InlineData("Some One <someone@example.org>", false)]
    [InlineData("", false)]
    public void TheContactIsOneEmailAddress(string contact, bool valid)
    {
        if (valid)
        {
            var identity = new SecIdentity(contact);
            Assert.Equal("AetherSpark " + contact, identity.UserAgent);
            Assert.DoesNotContain(contact, identity.ToString(), StringComparison.Ordinal);
        }
        else
        {
            Assert.Throws<InvalidOperationException>(() => new SecIdentity(contact));
        }
    }
}
