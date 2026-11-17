using System.Text.Json;
using CUE4Parse.UE4.Pak;
using CUE4Parse.UE4.Pak.Objects;
using CUE4Parse.UE4.Versions;

// Deliberately open only the fixed batch copy, with no provider, key search or extraction.
var batch = Path.GetFullPath("local/assets/batches/B20261116-M02C-01");
var input = Path.Combine(batch, "raw/GAME-LOCAL/pakchunk296-WindowsNoEditor.pak");
var output = Path.Combine(batch, "derived");
var result = new Dictionary<string, object?>
{
    ["started_at"] = DateTimeOffset.UtcNow,
    ["tool"] = "CUE4Parse",
    ["commit"] = "4d2301a8eb129f593f1c76ff82612400801f1a94",
    ["game_profile"] = "GAME_WutheringWaves",
    ["input"] = input,
    ["index_status"] = "not_attempted",
    ["export_status"] = "not_attempted",
    ["decode_status"] = "not_attempted",
    ["index_entry_count"] = null,
    ["stage"] = "construct_reader"
};
var exitCode = 0;
try
{
    using var reader = new PakFileReader(input, new VersionContainer(EGame.GAME_WutheringWaves));
    result["header"] = new
    {
        reader.Info.Magic,
        Version = reader.Info.Version.ToString(),
        reader.Info.IndexOffset,
        reader.Info.IndexSize,
        reader.Info.EncryptedIndex,
        EncryptionKeyGuid = reader.Info.EncryptionKeyGuid.ToString(),
        CompressionMethods = reader.Info.CompressionMethods.Select(x => x.ToString()).ToArray(),
        reader.IsEncrypted
    };
    result["stage"] = "mount_index";
    reader.Mount(StringComparer.OrdinalIgnoreCase);
    var entries = reader.Files.Values.OfType<FPakEntry>().Select(p => new
    {
        internal_path = p.Path,
        type = p.Extension,
        compressed_bytes = p.CompressedSize,
        uncompressed_bytes = p.UncompressedSize,
        compression = p.CompressionMethod.ToString(),
        encrypted = p.IsEncrypted,
        offset = p.Offset,
        compression_block_size = p.CompressionBlockSize,
        dependencies = (object?)null,
        dependency_status = "not_inspected"
    }).ToArray();
    File.WriteAllLines(Path.Combine(output, "container_entries.jsonl"), entries.Select(p => JsonSerializer.Serialize(p)));
    result["index_entry_count"] = entries.Length;
    result["index_status"] = "readable";
    result["stage"] = "index_complete";
}
catch (Exception e)
{
    result["index_status"] = "blocked";
    result["exception_type"] = e.GetType().FullName;
    result["exception_message"] = e.Message;
    result["exception_detail"] = e.ToString();
    exitCode = 2;
}
result["finished_at"] = DateTimeOffset.UtcNow;
File.WriteAllText(Path.Combine(output, "probe_tool_result.json"), JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true }));
Console.WriteLine(JsonSerializer.Serialize(result));
return exitCode;
