# Contracts

The shared types between the .NET and Python sides (ADR-0002 rule 2). Each JSON Schema here is the
source; the C# records and Pydantic models are generated from it and never edited by hand.

| Schema | Generated C# | Generated Python |
|---|---|---|
| `filing-job.schema.json`: the ingester's job for the pipeline (data-plane.md, section 3) | `src/dotnet/AetherSpark.Ingester/Contracts/FilingJob.g.cs` | `aetherspark_common.contracts.filing_job` |

## Changing a contract

1. Edit the schema.
2. Run `just contracts` to rewrite the generated files.
3. Add or adjust the shared cases in `examples/`, which both languages' tests read.
4. Commit all of it together. CI runs `just contracts --check` and fails when a generated file is stale.

A change that a reader of the current version cannot read raises the schema's `version` constant.

## What the generator reads

`generate.py` uses the standard library only and reads a small, fixed subset of JSON Schema:

- objects with `additionalProperties: false`, every property required;
- a `const` integer;
- a string with an anchored `pattern` (ASCII classes only, no `\d`, `\w` or `\s`), or an `enum` of strings;
- an integer with `minimum` and `maximum`;
- an array of one `$defs` object with `minItems` and `maxItems`.

It refuses anything else by name, so a schema never promises more than both languages check.
