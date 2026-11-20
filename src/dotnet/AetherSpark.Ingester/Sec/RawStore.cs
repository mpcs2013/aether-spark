using System.Globalization;
using System.Security.Cryptography;

namespace AetherSpark.Ingester.Sec;

/// <summary>A stored file: what the job contract and `ref.filing_files` record.</summary>
public sealed record StoredFile(string Name, string Kind, string Sha256, long Size);

/// <summary>
/// The raw folder (data-plane.md, decision 9): each filing's files at
/// `&lt;raw&gt;/&lt;cik&gt;/&lt;accession&gt;/&lt;name&gt;`, the layout the pipeline builds its paths
/// from. A file is written beside its place under a dot name, hashed while it is written, and moved
/// into place only when complete and within the size limit.
/// </summary>
public sealed class RawStore(IngesterSettings settings)
{
    private const int ChunkBytes = 81920;

    public string FolderFor(long cik, string accession)
    {
        ArgumentNullException.ThrowIfNull(settings);
        if (!SecNames.IsCik(cik) || !SecNames.IsAccession(accession))
        {
            throw new ArgumentException("not a CIK and an accession number");
        }

        return Path.Combine(Path.GetFullPath(settings.RawDirectory),
            cik.ToString(CultureInfo.InvariantCulture), accession);
    }

    public async Task<StoredFile> WriteAsync(long cik, string accession, ChosenFile file, Stream content,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(file);
        ArgumentNullException.ThrowIfNull(content);
        if (!SecNames.IsFileName(file.Name))
        {
            throw new ArgumentException("not a plain file name", nameof(file));
        }

        var folder = FolderFor(cik, accession);
        var target = Path.GetFullPath(Path.Combine(folder, file.Name));
        if (!string.Equals(Path.GetDirectoryName(target), folder, StringComparison.Ordinal))
        {
            throw new ArgumentException("the file would leave its filing's folder", nameof(file));
        }

        Directory.CreateDirectory(folder);
        var partial = Path.Combine(folder, "." + file.Name + ".part");
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        long size = 0;
        try
        {
            var stream = new FileStream(partial, FileMode.Create, FileAccess.Write, FileShare.None, ChunkBytes, useAsync: true);
            await using (stream.ConfigureAwait(false))
            {
                var buffer = new byte[ChunkBytes];
                int read;
                while ((read = await content.ReadAsync(buffer, cancellationToken).ConfigureAwait(false)) > 0)
                {
                    size += read;
                    if (size > settings.MaxFileBytes)
                    {
                        throw new FileTooLargeException($"{file.Name} is larger than {settings.MaxFileBytes} bytes");
                    }

                    hash.AppendData(buffer, 0, read);
                    await stream.WriteAsync(buffer.AsMemory(0, read), cancellationToken).ConfigureAwait(false);
                }
            }

            File.Move(partial, target, overwrite: true);
        }
        catch
        {
            File.Delete(partial);
            throw;
        }

        return new StoredFile(file.Name, file.Kind, Convert.ToHexStringLower(hash.GetHashAndReset()), size);
    }
}

/// <summary>A file beyond the size limit; its filing is refused.</summary>
public sealed class FileTooLargeException : Exception
{
    public FileTooLargeException()
    {
    }

    public FileTooLargeException(string message)
        : base(message)
    {
    }

    public FileTooLargeException(string message, Exception innerException)
        : base(message, innerException)
    {
    }
}
