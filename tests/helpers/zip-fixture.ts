// This helper builds a valid ZIP buffer in memory for use in tests.
//
// ZIP file layout (written in this order):
//   [Local file entries]   one per file: local header + filename + compressed data
//   [Central directory]    one record per file: metadata + offset back to local header
//   [EOCD]                 End of Central Directory — the ZIP parser's entry point
//
// ZIP parsers start from the end (EOCD), find the central directory, then
// jump to each local entry. This is why the format is described as "backwards".

import { deflateRawSync } from "node:zlib";

interface ZipFixtureEntry {
  name: string;
  data: Buffer | string;
  compress?: boolean; // defaults to true; set false for nested APKs inside XAPK
}

export function createZip(entries: ZipFixtureEntry[]): Buffer {
  const localChunks: Buffer[] = [];
  const centralChunks: Buffer[] = [];
  let localOffset = 0; // tracks byte offset of each local entry for the central directory

  for (const input of entries) {
    const name = Buffer.from(input.name.replaceAll("\\", "/"), "utf8");
    const data = Buffer.isBuffer(input.data) ? input.data : Buffer.from(input.data, "utf8");
    const compressed = input.compress === false ? data : deflateRawSync(data);
    const method = input.compress === false ? 0 : 8; // 0 = stored, 8 = deflate
    const crc = crc32(data);

    // Local file header (30 bytes) + filename + compressed data.
    // The local header duplicates most fields from the central directory record.
    const localHeader = Buffer.alloc(30);
    localHeader.writeUInt32LE(0x04034b50, 0);       // local file signature
    localHeader.writeUInt16LE(20, 4);               // version needed: 2.0
    localHeader.writeUInt16LE(0x0800, 6);           // flags: UTF-8 filename
    localHeader.writeUInt16LE(method, 8);           // compression method
    localHeader.writeUInt16LE(0, 10);               // last mod time
    localHeader.writeUInt16LE(0, 12);               // last mod date
    localHeader.writeUInt32LE(crc, 14);             // CRC-32 of uncompressed data
    localHeader.writeUInt32LE(compressed.length, 18);
    localHeader.writeUInt32LE(data.length, 22);     // uncompressed size
    localHeader.writeUInt16LE(name.length, 26);
    localHeader.writeUInt16LE(0, 28);               // extra field length

    localChunks.push(localHeader, name, compressed);

    // Central directory record (46 bytes) + filename.
    // Parsers read this to get the list of files and their offsets — they
    // never need to scan the local entries to discover what's in the archive.
    const centralHeader = Buffer.alloc(46);
    centralHeader.writeUInt32LE(0x02014b50, 0);     // central dir signature
    centralHeader.writeUInt16LE(20, 4);             // version made by
    centralHeader.writeUInt16LE(20, 6);             // version needed
    centralHeader.writeUInt16LE(0x0800, 8);         // flags: UTF-8
    centralHeader.writeUInt16LE(method, 10);
    centralHeader.writeUInt16LE(0, 12);             // last mod time
    centralHeader.writeUInt16LE(0, 14);             // last mod date
    centralHeader.writeUInt32LE(crc, 16);
    centralHeader.writeUInt32LE(compressed.length, 20);
    centralHeader.writeUInt32LE(data.length, 24);
    centralHeader.writeUInt16LE(name.length, 28);
    centralHeader.writeUInt16LE(0, 30);             // extra field length
    centralHeader.writeUInt16LE(0, 32);             // comment length
    centralHeader.writeUInt16LE(0, 34);             // disk number start
    centralHeader.writeUInt16LE(0, 36);             // internal attributes
    centralHeader.writeUInt32LE(0, 38);             // external attributes
    centralHeader.writeUInt32LE(localOffset, 42);   // offset of local header
    centralChunks.push(centralHeader, name);

    localOffset += localHeader.length + name.length + compressed.length;
  }

  const localData = Buffer.concat(localChunks);
  const centralDirectory = Buffer.concat(centralChunks);

  // End of Central Directory (22 bytes) — the parser finds this by scanning
  // backwards from the end of the file for the 0x06054b50 signature.
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);               // EOCD signature
  eocd.writeUInt16LE(0, 4);                        // disk number
  eocd.writeUInt16LE(0, 6);                        // disk with central dir
  eocd.writeUInt16LE(entries.length, 8);           // entries on this disk
  eocd.writeUInt16LE(entries.length, 10);          // total entries
  eocd.writeUInt32LE(centralDirectory.length, 12); // central dir size
  eocd.writeUInt32LE(localData.length, 16);        // central dir offset
  eocd.writeUInt16LE(0, 20);                       // comment length

  return Buffer.concat([localData, centralDirectory, eocd]);
}

// Standard CRC-32 using the 0xEDB88320 polynomial (reflected representation).
// Used by the ZIP format to verify file integrity.
function crc32(buffer: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}
