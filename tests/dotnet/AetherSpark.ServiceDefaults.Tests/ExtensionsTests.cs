using System.Net;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;
using Microsoft.AspNetCore.Http.Features;
using Microsoft.Extensions.DependencyInjection;

namespace AetherSpark.ServiceDefaults.Tests;

public sealed class ExtensionsTests
{
    [Theory]
    [InlineData("Development", HttpStatusCode.OK)]
    [InlineData("Production", HttpStatusCode.NotFound)]
    public async Task HealthEndpointsAreMappedInDevelopmentOnly(
        string environment, HttpStatusCode expected)
    {
        var builder = WebApplication.CreateBuilder(new WebApplicationOptions
        {
            EnvironmentName = environment,
        });
        builder.WebHost.UseUrls("http://127.0.0.1:0");
        builder.AddServiceDefaults();

        await using var app = builder.Build();
        app.MapDefaultEndpoints();
        await app.StartAsync(TestContext.Current.CancellationToken);

        var address = app.Services.GetRequiredService<IServer>()
            .Features.GetRequiredFeature<IServerAddressesFeature>().Addresses.Single();
        using var client = new HttpClient { BaseAddress = new Uri(address) };

        var health = await client.GetAsync("/health", TestContext.Current.CancellationToken);
        var alive = await client.GetAsync("/alive", TestContext.Current.CancellationToken);

        Assert.Equal(expected, health.StatusCode);
        Assert.Equal(expected, alive.StatusCode);
    }
}
