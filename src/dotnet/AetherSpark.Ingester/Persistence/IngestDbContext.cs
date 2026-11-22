using Microsoft.EntityFrameworkCore;

namespace AetherSpark.Ingester.Persistence;

/// <summary>
/// The ingester's tables in the data Postgres: `ref` (companies, tickers, filings) and `ingest_ops`
/// (the pipeline's jobs). The migrations are applied as SQL files by the server's own start
/// (deploy/compose/stack/postgres-data/migrations/), as the migration owner; this program never
/// changes a table (data-plane.md, decision 7).
/// </summary>
public sealed class IngestDbContext(DbContextOptions<IngestDbContext> options) : DbContext(options)
{
    public const string HistorySchema = "meta";
    public const string HistoryTable = "ef_migrations";

    // The forms the formats below admit; the ingester checks every value before it writes one
    private const string AccessionPattern = "^[0-9]{10}-[0-9]{2}-[0-9]{6}$";
    private const string FileNamePattern = "^[A-Za-z0-9._-]+$";
    private const string Sha256Pattern = "^[0-9a-f]{64}$";

    public DbSet<Issuer> Issuers => Set<Issuer>();

    public DbSet<Ticker> Tickers => Set<Ticker>();

    public DbSet<Filing> Filings => Set<Filing>();

    public DbSet<FilingCompany> FilingCompanies => Set<FilingCompany>();

    public DbSet<FilingFile> FilingFiles => Set<FilingFile>();

    public DbSet<JobRun> JobRuns => Set<JobRun>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        ArgumentNullException.ThrowIfNull(modelBuilder);

        modelBuilder.Entity<Issuer>(e =>
        {
            e.ToTable("issuers", "ref", t => t.HasCheckConstraint("ck_issuers_cik", "cik BETWEEN 1 AND 9999999999"));
            e.HasKey(x => x.Cik);
            e.Property(x => x.Cik).HasColumnName("cik").ValueGeneratedNever();
            e.Property(x => x.Name).HasColumnName("name");
            e.Property(x => x.FirstSeenAt).HasColumnName("first_seen_at");
            e.Property(x => x.UpdatedAt).HasColumnName("updated_at");
        });

        modelBuilder.Entity<Ticker>(e =>
        {
            e.ToTable("tickers", "ref", t => t.HasCheckConstraint("ck_tickers_validity", "valid_to IS NULL OR valid_to >= valid_from"));
            e.HasKey(x => new { x.Cik, x.Symbol, x.Exchange, x.ValidFrom });
            e.Property(x => x.Cik).HasColumnName("cik");
            e.Property(x => x.Symbol).HasColumnName("symbol");
            e.Property(x => x.Exchange).HasColumnName("exchange");
            e.Property(x => x.ValidFrom).HasColumnName("valid_from");
            e.Property(x => x.ValidTo).HasColumnName("valid_to");
            e.HasOne<Issuer>().WithMany().HasForeignKey(x => x.Cik).OnDelete(DeleteBehavior.Restrict);
            e.HasIndex(x => x.Symbol);
        });

        modelBuilder.Entity<Filing>(e =>
        {
            e.ToTable("filings", "ref", t => t.HasCheckConstraint("ck_filings_accession_no", $"accession_no ~ '{AccessionPattern}'"));
            e.HasKey(x => x.AccessionNo);
            e.Property(x => x.AccessionNo).HasColumnName("accession_no");
            e.Property(x => x.Cik).HasColumnName("cik");
            e.Property(x => x.Form).HasColumnName("form");
            e.Property(x => x.KnownAt).HasColumnName("known_at");
            e.Property(x => x.FiledDate).HasColumnName("filed_date");
            e.Property(x => x.PeriodOfReport).HasColumnName("period_of_report");
            e.Property(x => x.StoredAt).HasColumnName("stored_at");
            e.HasOne<Issuer>().WithMany().HasForeignKey(x => x.Cik).OnDelete(DeleteBehavior.Restrict);
            e.HasMany(x => x.Files).WithOne().HasForeignKey(x => x.AccessionNo).OnDelete(DeleteBehavior.Restrict);
            e.HasIndex(x => new { x.Cik, x.KnownAt });
        });

        modelBuilder.Entity<FilingCompany>(e =>
        {
            e.ToTable("filing_companies", "ref");
            e.HasKey(x => new { x.AccessionNo, x.Cik });
            e.Property(x => x.AccessionNo).HasColumnName("accession_no");
            e.Property(x => x.Cik).HasColumnName("cik");
            e.Property(x => x.FirstSeenAt).HasColumnName("first_seen_at");
            e.HasOne<Filing>().WithMany().HasForeignKey(x => x.AccessionNo).OnDelete(DeleteBehavior.Restrict);
            e.HasOne<Issuer>().WithMany().HasForeignKey(x => x.Cik).OnDelete(DeleteBehavior.Restrict);
            e.HasIndex(x => x.Cik);
        });

        modelBuilder.Entity<FilingFile>(e =>
        {
            e.ToTable("filing_files", "ref", t =>
            {
                t.HasCheckConstraint("ck_filing_files_name", $"name ~ '{FileNamePattern}'");
                t.HasCheckConstraint("ck_filing_files_kind", "kind IN ('primary', 'instance', 'schema', 'linkbase')");
                t.HasCheckConstraint("ck_filing_files_sha256", $"sha256 ~ '{Sha256Pattern}'");
                t.HasCheckConstraint("ck_filing_files_size", "size >= 0");
            });
            e.HasKey(x => new { x.AccessionNo, x.Name });
            e.Property(x => x.AccessionNo).HasColumnName("accession_no");
            e.Property(x => x.Name).HasColumnName("name");
            e.Property(x => x.Kind).HasColumnName("kind");
            e.Property(x => x.Sha256).HasColumnName("sha256");
            e.Property(x => x.Size).HasColumnName("size");
        });

        modelBuilder.Entity<JobRun>(e =>
        {
            e.ToTable("job_runs", "ingest_ops", t =>
            {
                t.HasCheckConstraint("ck_job_runs_status", "status IN ('queued', 'running', 'done', 'failed')");
                t.HasCheckConstraint("ck_job_runs_attempts", "attempts >= 0");
            });
            e.HasKey(x => x.Id);
            e.Property(x => x.Id).HasColumnName("id").UseIdentityAlwaysColumn();
            e.Property(x => x.AccessionNo).HasColumnName("accession_no");
            e.Property(x => x.Contract).HasColumnName("contract").HasColumnType("jsonb");
            e.Property(x => x.Status).HasColumnName("status").HasDefaultValue("queued");
            e.Property(x => x.Reason).HasColumnName("reason");
            e.Property(x => x.Attempts).HasColumnName("attempts").HasDefaultValue(0);
            e.Property(x => x.CreatedAt).HasColumnName("created_at");
            e.Property(x => x.StartedAt).HasColumnName("started_at");
            e.Property(x => x.FinishedAt).HasColumnName("finished_at");
            e.HasOne<Filing>().WithMany().HasForeignKey(x => x.AccessionNo).OnDelete(DeleteBehavior.Restrict);
            e.HasIndex(x => x.AccessionNo).IsUnique();
            e.HasIndex(x => x.Status);
        });
    }
}
