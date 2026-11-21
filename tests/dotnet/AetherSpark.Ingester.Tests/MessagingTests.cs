using Wolverine;

namespace AetherSpark.Ingester.Tests;

/// <summary>
/// Wolverine's settings that a short test walk cannot show. The first real walk (2026-11-21) found
/// one: Wolverine cancels a local queue's handler after 60 seconds by default, and a company's walk
/// takes minutes, so every company ended in the error queue after about a minute.
/// </summary>
public sealed class MessagingTests
{
    [Fact]
    public void WolverinesOwnDefaultWouldCutACompanysWalkAfterAMinute()
    {
        // The reason for the setting: if Wolverine's default changes, this test says so
        Assert.Equal(TimeSpan.FromSeconds(60), new WolverineOptions().DefaultExecutionTimeout);
    }

    [Fact]
    public void AHandlerMayRunForHoursSoACompanysWalkIsNotCut()
    {
        var folder = SecTesting.TempFolder();
        var password = Path.Combine(folder, "password");
        File.WriteAllText(password, "unused");
        var settings = new IngesterSettings { DatabaseHost = "unused.invalid", DatabasePasswordFile = password };
        var opts = new WolverineOptions();

        Messaging.Configure(opts, settings);

        Assert.Equal(Messaging.HandlerTimeout, opts.DefaultExecutionTimeout);
        Assert.True(opts.DefaultExecutionTimeout >= TimeSpan.FromHours(1));
    }
}
