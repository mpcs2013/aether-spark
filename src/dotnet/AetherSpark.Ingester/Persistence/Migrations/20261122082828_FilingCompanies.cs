using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace AetherSpark.Ingester.Persistence.Migrations
{
    /// <inheritdoc />
    public partial class FilingCompanies : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "filing_companies",
                schema: "ref",
                columns: table => new
                {
                    accession_no = table.Column<string>(type: "text", nullable: false),
                    cik = table.Column<long>(type: "bigint", nullable: false),
                    first_seen_at = table.Column<DateTimeOffset>(type: "timestamp with time zone", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_filing_companies", x => new { x.accession_no, x.cik });
                    table.ForeignKey(
                        name: "FK_filing_companies_filings_accession_no",
                        column: x => x.accession_no,
                        principalSchema: "ref",
                        principalTable: "filings",
                        principalColumn: "accession_no",
                        onDelete: ReferentialAction.Restrict);
                    table.ForeignKey(
                        name: "FK_filing_companies_issuers_cik",
                        column: x => x.cik,
                        principalSchema: "ref",
                        principalTable: "issuers",
                        principalColumn: "cik",
                        onDelete: ReferentialAction.Restrict);
                });

            migrationBuilder.CreateIndex(
                name: "IX_filing_companies_cik",
                schema: "ref",
                table: "filing_companies",
                column: "cik");

            // Every filing stored so far is listed by the company it is stored under; the other
            // companies of a joint filing are added by their next walk (no request needed)
            migrationBuilder.Sql(
                "INSERT INTO ref.filing_companies (accession_no, cik, first_seen_at) "
                + "SELECT accession_no, cik, stored_at FROM ref.filings;");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "filing_companies",
                schema: "ref");
        }
    }
}
