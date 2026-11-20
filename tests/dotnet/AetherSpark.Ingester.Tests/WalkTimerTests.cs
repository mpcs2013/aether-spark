using AetherSpark.Ingester.Walk;

namespace AetherSpark.Ingester.Tests;

public sealed class WalkTimerTests
{
    private static readonly TimeOnly FourUtc = new(4, 0);

    [Theory]
    [InlineData("2026-11-19T03:00:00Z", 60)]
    [InlineData("2026-11-19T04:00:00Z", 24 * 60)]
    [InlineData("2026-11-19T04:00:01Z", 24 * 60 - 1.0 / 60)]
    [InlineData("2026-11-19T22:30:00Z", 5 * 60 + 30)]
    // The local offset never matters: 23:00 at UTC-5 is 04:00 UTC
    [InlineData("2026-11-19T23:00:00-05:00", 24 * 60)]
    public void TheNextWalkIsAtFourUtc(string now, double minutes)
    {
        var until = WalkTimer.Until(DateTimeOffset.Parse(now, System.Globalization.CultureInfo.InvariantCulture), FourUtc);
        Assert.Equal(TimeSpan.FromMinutes(minutes), until);
    }

    [Fact]
    public void ADaylightSavingChangeInTheUsDoesNotMoveIt()
    {
        // 2026-11-01 is the US fall-back day; the walk stays at 04:00 UTC
        var until = WalkTimer.Until(new DateTimeOffset(2026, 11, 1, 5, 0, 0, TimeSpan.Zero), FourUtc);
        Assert.Equal(TimeSpan.FromHours(23), until);
    }
}
