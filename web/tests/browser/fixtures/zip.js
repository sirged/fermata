// A tiny, self-contained ZIP reader and writer for tests that need to build
// or round-trip a real archive without a new dependency - the same
// discipline navigation-score.js's own buildMxl() already applies to a
// single fixed pair of files. This one is generic (any number of named
// entries) so data-portability.spec.js can read a REAL exported archive's
// manifest.json, edit it, and re-pack the same files/ bytes into a new
// archive - never hand-assembling a whole archive's worth of rows that
// only the server's own export already knows how to produce correctly.
import zlib from "node:zlib";

/** Build a real ZIP archive (DEFLATE + a proper central directory) from
 * `entries`, an array of {name, data: Buffer}. See buildMxl in
 * navigation-score.js for the twin of this for the one fixed pair of files
 * that fixture needs; this is the same format, generalised. */
export function buildZip(entries) {
  const locals = [];
  const central = [];
  let offset = 0;
  for (const file of entries) {
    const name = Buffer.from(file.name, "utf-8");
    const deflated = zlib.deflateRawSync(file.data);
    const crc = zlib.crc32 ? zlib.crc32(file.data) : crc32(file.data);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4); // version needed
    local.writeUInt16LE(0, 6); // flags - no data descriptor, so sizes are real
    local.writeUInt16LE(8, 8); // deflate
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(deflated.length, 18);
    local.writeUInt32LE(file.data.length, 22);
    local.writeUInt16LE(name.length, 26);
    locals.push(local, name, deflated);

    const entry = Buffer.alloc(46);
    entry.writeUInt32LE(0x02014b50, 0);
    entry.writeUInt16LE(20, 4); // version made by
    entry.writeUInt16LE(20, 6); // version needed
    entry.writeUInt16LE(0, 8);
    entry.writeUInt16LE(8, 10);
    entry.writeUInt32LE(crc, 16);
    entry.writeUInt32LE(deflated.length, 20);
    entry.writeUInt32LE(file.data.length, 24);
    entry.writeUInt16LE(name.length, 28);
    entry.writeUInt32LE(offset, 42);
    central.push(entry, name);
    offset += local.length + name.length + deflated.length;
  }
  const centralBuffer = Buffer.concat(central);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(entries.length, 8);
  eocd.writeUInt16LE(entries.length, 10);
  eocd.writeUInt32LE(centralBuffer.length, 12);
  eocd.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, centralBuffer, eocd]);
}

/** Read a real ZIP archive back into {name, data} entries - a real parse of
 * the central directory and each entry's local header (store OR deflate),
 * not a guess at fixed offsets. Used only to round-trip an archive this
 * suite itself downloaded a moment earlier (a real Fermata export written
 * by Python's own zipfile), never anything untrusted. */
export function readZip(buffer) {
  const EOCD_SIZE = 22;
  // Every archive this reads (this suite's own downloads) is a plain zip
  // with no trailing comment, so the End Of Central Directory record is
  // always exactly the last 22 bytes.
  const eocdOffset = buffer.length - EOCD_SIZE;
  if (eocdOffset < 0 || buffer.readUInt32LE(eocdOffset) !== 0x06054b50) {
    throw new Error("readZip: no end-of-central-directory record at the expected offset");
  }
  const entryCount = buffer.readUInt16LE(eocdOffset + 10);
  const centralDirOffset = buffer.readUInt32LE(eocdOffset + 16);

  const out = [];
  let pos = centralDirOffset;
  for (let i = 0; i < entryCount; i++) {
    if (buffer.readUInt32LE(pos) !== 0x02014b50) {
      throw new Error(`readZip: central directory entry ${i} has a bad signature`);
    }
    const method = buffer.readUInt16LE(pos + 10);
    const compressedSize = buffer.readUInt32LE(pos + 20);
    const uncompressedSize = buffer.readUInt32LE(pos + 24);
    const nameLen = buffer.readUInt16LE(pos + 28);
    const extraLen = buffer.readUInt16LE(pos + 30);
    const commentLen = buffer.readUInt16LE(pos + 32);
    const localOffset = buffer.readUInt32LE(pos + 42);
    const name = buffer.toString("utf-8", pos + 46, pos + 46 + nameLen);
    pos += 46 + nameLen + extraLen + commentLen;

    if (buffer.readUInt32LE(localOffset) !== 0x04034b50) {
      throw new Error(`readZip: local file header for ${name} has a bad signature`);
    }
    const localNameLen = buffer.readUInt16LE(localOffset + 26);
    const localExtraLen = buffer.readUInt16LE(localOffset + 28);
    const dataStart = localOffset + 30 + localNameLen + localExtraLen;
    const raw = buffer.subarray(dataStart, dataStart + compressedSize);
    const data =
      method === 0
        ? Buffer.from(raw)
        : zlib.inflateRawSync(raw, { maxOutputLength: uncompressedSize + 1 });
    out.push({ name, data });
  }
  return out;
}

// node's zlib.crc32 only arrived in 22.2; this is the same polynomial, for
// older runners - copied from navigation-score.js's own fallback rather than
// imported, since that one is not exported.
function crc32(buffer) {
  let crc = ~0;
  for (const byte of buffer) {
    crc ^= byte;
    for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return (~crc) >>> 0;
}
