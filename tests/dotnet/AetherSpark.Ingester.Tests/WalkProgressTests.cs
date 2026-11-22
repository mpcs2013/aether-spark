using AetherSpark.Ingester.Walk;
using Microsoft.Extensions.Logging;

namespace AetherSpark.Ingester.Tests;

/// <summary>The walk's end line (2026-11-22): after its last company, with the walk's sums and duration.</summary>
public sealed class WalkProgressTests
{
    private sealed class Clock(DateTimeOffset start) : TimeProvider
    {
        public DateTimeOffset Now { get; set; } = start;

        public override DateTimeOffset GetUtcNow() => Now;
    }

    private sealed class Lines : ILogger<WalkProgress>
    {
        public List<string> All { get; } = [];

        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;

        public bool IsEnabled(LogLevel logLevel) => true;

        public void Log<TState>(LogLevel logLevel, EventId eventId, TState state, Exception? exception,
            Func<TState, Exception?, string> formatter) => All.Add(formatter(state, exception));
    }

    [Fact]
    public void TheEndIsLoggedAfterTheLastCompanyWithTheWalksSums()
    {
        var clock = new Clock(new DateTimeOffset(2026, 11, 22, 8, 4, 36, TimeSpan.Zero));
        var log = new Lines();
        var progress = new WalkProgress(clock, log);
        var walk = Guid.NewGuid();
        var results = new[]
        {
            new CompanyResult(HandedOff: 2, Known: 40, Refused: 1, Stopped: false),
            new CompanyResult(HandedOff: 0, Known: 0, Refused: 0, Stopped: false, Shared: 31),
            new CompanyResult(HandedOff: 0, Known: 0, Refused: 0, Stopped: false, CompanyRefused: true),
        };

        for (var i = 0; i < results.Length; i++)
        {
            var message = new FetchCompany(100 + i, walk, "start", results.Length);
            progress.Starting(message);
            clock.Now = clock.Now.AddSeconds(76);
            Assert.Empty(log.All);
            progress.Finished(message, results[i]);
        }

        Assert.Equal(
            "universe walk (start) done: 3 companies in 3 min 48 s; 2 filings handed off, 40 already stored, "
            + "31 stored under another company, 1 refused; 1 companies refused, 0 stopped",
            Assert.Single(log.All));
    }

    [Fact]
    public void AMessageWithoutItsWalkCountsTowardNone()
    {
        // As stored before 2026-11-22, or recovered from a run that kept the walk's other companies
        var log = new Lines();
        var progress = new WalkProgress(TimeProvider.System, log);
        var old = new FetchCompany(100);
        progress.Starting(old);
        progress.Finished(old, new CompanyResult(1, 2, 0, false));

        var walk = Guid.NewGuid();
        progress.Finished(new FetchCompany(101, walk, "daily", 2), new CompanyResult(0, 5, 0, true));

        Assert.Empty(log.All);
    }

    [Theory]
    [InlineData(0, "0 s")]
    [InlineData(48.4, "48 s")]
    [InlineData(228, "3 min 48 s")]
    [InlineData(3599, "59 min 59 s")]
    [InlineData(18420, "5 h 07 min")]
    public void TheDurationReadsPlainly(double seconds, string expected) =>
        Assert.Equal(expected, WalkProgress.Took(TimeSpan.FromSeconds(seconds)));
}
