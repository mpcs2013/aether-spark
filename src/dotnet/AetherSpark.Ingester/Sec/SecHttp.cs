using System.Net;
using Microsoft.Extensions.Http.Resilience;
using Polly;

namespace AetherSpark.Ingester.Sec;

/// <summary>
/// The SEC's HTTP client (data-plane.md, section 3), built here rather than by the client factory,
/// so ServiceDefaults' standard resilience handler never doubles its retries and no logging handler
/// sees its headers. From the outside in: a total timeout, retries with exponential backoff and
/// jitter on 429, 408, 5xx and no answer (honouring Retry-After, at most two minutes), a timeout per
/// attempt, the pace before each attempt, then the network. Redirects are not followed, cookies not
/// kept; the proxy is the configured one or none, never one from the environment.
/// </summary>
public static class SecHttp
{
    public const int MaxRetries = 4;

    public static IServiceCollection AddSecClient(this IServiceCollection services, IngesterSettings settings)
    {
        ArgumentNullException.ThrowIfNull(settings);
        services.AddSingleton(sp => new SecPace(sp.GetRequiredService<TimeProvider>(), settings.RequestSpacing));
        services.AddSingleton<SecStop>();
        services.AddSingleton(_ => SecIdentity.FromFile(settings.SecContactFile));
        services.AddSingleton<RawStore>();
        services.AddSingleton(sp => new SecClient(
            CreateClient(settings, sp.GetRequiredService<SecIdentity>(), sp.GetRequiredService<SecPace>(), PrimaryHandler(settings)),
            settings, sp.GetRequiredService<SecStop>()));
        return services;
    }

    /// <summary>The client over any network handler; the tests pass a scripted one.</summary>
    public static HttpClient CreateClient(IngesterSettings settings, SecIdentity identity, SecPace pace, HttpMessageHandler network)
    {
        ArgumentNullException.ThrowIfNull(settings);
        ArgumentNullException.ThrowIfNull(identity);
        var pipeline = new ResiliencePipelineBuilder<HttpResponseMessage>()
            .AddTimeout(TimeSpan.FromMinutes(5))
            .AddRetry(new HttpRetryStrategyOptions
            {
                MaxRetryAttempts = MaxRetries,
                BackoffType = DelayBackoffType.Exponential,
                UseJitter = true,
                Delay = settings.RetryDelay,
                MaxDelay = TimeSpan.FromMinutes(2),
                ShouldRetryAfterHeader = true,
            })
            .AddTimeout(TimeSpan.FromSeconds(60))
            .Build();
#pragma warning disable CA2000 // The client owns and disposes the handler chain
        var client = new HttpClient(new ResilienceHandler(pipeline)
        {
            InnerHandler = new SecPacingHandler(pace) { InnerHandler = network },
        })
        {
            Timeout = Timeout.InfiniteTimeSpan,
        };
#pragma warning restore CA2000
        client.DefaultRequestHeaders.TryAddWithoutValidation("User-Agent", identity.UserAgent);
        return client;
    }

    public static SocketsHttpHandler PrimaryHandler(IngesterSettings settings)
    {
        ArgumentNullException.ThrowIfNull(settings);
        return new SocketsHttpHandler
        {
            AllowAutoRedirect = false,
            UseProxy = settings.Proxy is not null,
            Proxy = settings.Proxy is null ? null : new WebProxy(settings.Proxy),
            UseCookies = false,
            AutomaticDecompression = DecompressionMethods.All,
            ConnectTimeout = TimeSpan.FromSeconds(15),
            PooledConnectionLifetime = TimeSpan.FromMinutes(5),
        };
    }
}
