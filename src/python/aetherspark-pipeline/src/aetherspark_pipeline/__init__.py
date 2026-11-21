"""The pipeline worker (docs/architecture/data-plane.md, section 4).

It takes the ingester's filing jobs from `ingest_ops.job_runs`, one at a time: it checks the job's
contract, copies the named files from the raw folder into an in-memory job folder and hashes them
again, then parses them in a child process with memory, CPU and time limits. PR 4a stops after a
hardened pre-parse of the XML files; Arelle, normalising and loading come with PR 4b.
"""
