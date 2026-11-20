using System;
using Microsoft.EntityFrameworkCore.Migrations;
using Npgsql.EntityFrameworkCore.PostgreSQL.Metadata;

#nullable disable

namespace AetherSpark.Ingester.Persistence.Migrations
{
    /// <inheritdoc />
    public partial class Initial : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.EnsureSchema(
                name: "ref");

            migrationBuilder.EnsureSchema(
                name: "ingest_ops");

            migrationBuilder.CreateTable(
                name: "issuers",
                schema: "ref",
                columns: table => new
                {
                    cik = table.Column<long>(type: "bigint", nullable: false),
                    name = table.Column<string>(type: "text", nullable: false),
                    first_seen_at = table.Column<DateTimeOffset>(type: "timestamp with time zone", nullable: false),
                    updated_at = table.Column<DateTimeOffset>(type: "timestamp with time zone", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_issuers", x => x.cik);
                    table.CheckConstraint("ck_issuers_cik", "cik BETWEEN 1 AND 9999999999");
                });

            migrationBuilder.CreateTable(
                name: "filings",
                schema: "ref",
                columns: table => new
                {
                    accession_no = table.Column<string>(type: "text", nullable: false),
                    cik = table.Column<long>(type: "bigint", nullable: false),
                    form = table.Column<string>(type: "text", nullable: false),
                    known_at = table.Column<DateTimeOffset>(type: "timestamp with time zone", nullable: false),
                    filed_date = table.Column<DateOnly>(type: "date", nullable: false),
                    period_of_report = table.Column<DateOnly>(type: "date", nullable: true),
                    stored_at = table.Column<DateTimeOffset>(type: "timestamp with time zone", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_filings", x => x.accession_no);
                    table.CheckConstraint("ck_filings_accession_no", "accession_no ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'");
                    table.ForeignKey(
                        name: "FK_filings_issuers_cik",
                        column: x => x.cik,
                        principalSchema: "ref",
                        principalTable: "issuers",
                        principalColumn: "cik",
                        onDelete: ReferentialAction.Restrict);
                });

            migrationBuilder.CreateTable(
                name: "tickers",
                schema: "ref",
                columns: table => new
                {
                    cik = table.Column<long>(type: "bigint", nullable: false),
                    symbol = table.Column<string>(type: "text", nullable: false),
                    exchange = table.Column<string>(type: "text", nullable: false),
                    valid_from = table.Column<DateOnly>(type: "date", nullable: false),
                    valid_to = table.Column<DateOnly>(type: "date", nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_tickers", x => new { x.cik, x.symbol, x.exchange, x.valid_from });
                    table.CheckConstraint("ck_tickers_validity", "valid_to IS NULL OR valid_to >= valid_from");
                    table.ForeignKey(
                        name: "FK_tickers_issuers_cik",
                        column: x => x.cik,
                        principalSchema: "ref",
                        principalTable: "issuers",
                        principalColumn: "cik",
                        onDelete: ReferentialAction.Restrict);
                });

            migrationBuilder.CreateTable(
                name: "filing_files",
                schema: "ref",
                columns: table => new
                {
                    accession_no = table.Column<string>(type: "text", nullable: false),
                    name = table.Column<string>(type: "text", nullable: false),
                    kind = table.Column<string>(type: "text", nullable: false),
                    sha256 = table.Column<string>(type: "text", nullable: false),
                    size = table.Column<long>(type: "bigint", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_filing_files", x => new { x.accession_no, x.name });
                    table.CheckConstraint("ck_filing_files_kind", "kind IN ('primary', 'instance', 'schema', 'linkbase')");
                    table.CheckConstraint("ck_filing_files_name", "name ~ '^[A-Za-z0-9._-]+$'");
                    table.CheckConstraint("ck_filing_files_sha256", "sha256 ~ '^[0-9a-f]{64}$'");
                    table.CheckConstraint("ck_filing_files_size", "size >= 0");
                    table.ForeignKey(
                        name: "FK_filing_files_filings_accession_no",
                        column: x => x.accession_no,
                        principalSchema: "ref",
                        principalTable: "filings",
                        principalColumn: "accession_no",
                        onDelete: ReferentialAction.Restrict);
                });

            migrationBuilder.CreateTable(
                name: "job_runs",
                schema: "ingest_ops",
                columns: table => new
                {
                    id = table.Column<long>(type: "bigint", nullable: false)
                        .Annotation("Npgsql:ValueGenerationStrategy", NpgsqlValueGenerationStrategy.IdentityAlwaysColumn),
                    accession_no = table.Column<string>(type: "text", nullable: false),
                    contract = table.Column<string>(type: "jsonb", nullable: false),
                    status = table.Column<string>(type: "text", nullable: false, defaultValue: "queued"),
                    reason = table.Column<string>(type: "text", nullable: true),
                    attempts = table.Column<int>(type: "integer", nullable: false, defaultValue: 0),
                    created_at = table.Column<DateTimeOffset>(type: "timestamp with time zone", nullable: false),
                    started_at = table.Column<DateTimeOffset>(type: "timestamp with time zone", nullable: true),
                    finished_at = table.Column<DateTimeOffset>(type: "timestamp with time zone", nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_job_runs", x => x.id);
                    table.CheckConstraint("ck_job_runs_attempts", "attempts >= 0");
                    table.CheckConstraint("ck_job_runs_status", "status IN ('queued', 'running', 'done', 'failed')");
                    table.ForeignKey(
                        name: "FK_job_runs_filings_accession_no",
                        column: x => x.accession_no,
                        principalSchema: "ref",
                        principalTable: "filings",
                        principalColumn: "accession_no",
                        onDelete: ReferentialAction.Restrict);
                });

            migrationBuilder.CreateIndex(
                name: "IX_filings_cik_known_at",
                schema: "ref",
                table: "filings",
                columns: new[] { "cik", "known_at" });

            migrationBuilder.CreateIndex(
                name: "IX_job_runs_accession_no",
                schema: "ingest_ops",
                table: "job_runs",
                column: "accession_no",
                unique: true);

            migrationBuilder.CreateIndex(
                name: "IX_job_runs_status",
                schema: "ingest_ops",
                table: "job_runs",
                column: "status");

            migrationBuilder.CreateIndex(
                name: "IX_tickers_symbol",
                schema: "ref",
                table: "tickers",
                column: "symbol");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "filing_files",
                schema: "ref");

            migrationBuilder.DropTable(
                name: "job_runs",
                schema: "ingest_ops");

            migrationBuilder.DropTable(
                name: "tickers",
                schema: "ref");

            migrationBuilder.DropTable(
                name: "filings",
                schema: "ref");

            migrationBuilder.DropTable(
                name: "issuers",
                schema: "ref");
        }
    }
}
