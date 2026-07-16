// This helper builds a valid Android binary XML (AXML) buffer in memory,
// mimicking what the Android build tools produce when compiling a manifest.
//
// AXML file layout:
//   [File header]     8 bytes — magic type + total size
//   [String pool]     all strings in the file, stored once
//   [Element chunks]  interleaved start/end element events (like SAX)
//
// Every chunk starts with the same 8-byte header:
//   offset 0: type       (uint16)
//   offset 2: headerSize (uint16)
//   offset 4: totalSize  (uint32)

const RES_XML_TYPE = 0x0003;               // file header type
const RES_STRING_POOL_TYPE = 0x0001;       // string pool chunk type
const RES_XML_START_ELEMENT_TYPE = 0x0102; // opening tag
const RES_XML_END_ELEMENT_TYPE = 0x0103;   // closing tag

// Sentinel meaning "no string" — used for namespaces and missing raw values.
const NO_INDEX = 0xffffffff;

// Attribute value types (only the ones we use in fixtures).
const TYPE_STRING = 0x03;      // value is a string pool index
const TYPE_INT_DEC = 0x10;     // value is a decimal integer
const TYPE_INT_BOOLEAN = 0x12; // value is 0 (false) or non-zero (true)

interface AttributeInput {
  name: string;
  value: string | number | boolean;
}

interface ElementInput {
  name: string;
  attributes?: AttributeInput[];
  children?: ElementInput[];
}

export function createBinaryManifest(options?: {
  packageName?: string;
  splitName?: string;
}): Buffer {
  const packageName = options?.packageName ?? "com.example.game";
  const manifestAttributes: AttributeInput[] = [
    { name: "package", value: packageName },
    { name: "versionCode", value: 514 },
    { name: "versionName", value: "5.14" },
  ];
  if (options?.splitName) {
    manifestAttributes.push({ name: "split", value: options.splitName });
  }

  const root: ElementInput = {
    name: "manifest",
    attributes: manifestAttributes,
    children: [
      {
        name: "uses-sdk",
        attributes: [
          { name: "minSdkVersion", value: 24 },
          { name: "targetSdkVersion", value: 35 },
        ],
      },
      {
        name: "uses-permission",
        attributes: [{ name: "name", value: "android.permission.INTERNET" }],
      },
      {
        name: "application",
        attributes: [
          { name: "label", value: "Goods Sorting" },
          { name: "debuggable", value: false },
          { name: "allowBackup", value: true },
        ],
        children: [
          {
            name: "activity",
            attributes: [
              { name: "name", value: "com.google.firebase.MessagingUnityPlayerActivity" },
              { name: "exported", value: true },
            ],
            children: [
              {
                name: "intent-filter",
                children: [
                  {
                    name: "action",
                    attributes: [{ name: "name", value: "android.intent.action.MAIN" }],
                  },
                  {
                    name: "category",
                    attributes: [
                      { name: "name", value: "android.intent.category.LAUNCHER" },
                    ],
                  },
                ],
              },
            ],
          },
        ],
      },
    ],
  };

  // Collect every unique string used anywhere in the tree into a single pool.
  // The parser uses integer indexes into this pool — no strings are stored inline.
  const strings = collectStrings(root);
  const stringIndexes = new Map(strings.map((value, index) => [value, index]));

  const chunks: Buffer[] = [createStringPool(strings)];
  appendElementChunks(root, stringIndexes, chunks);

  const body = Buffer.concat(chunks);

  // File header: marks this as a valid AXML file and stores the total size.
  const header = Buffer.alloc(8);
  header.writeUInt16LE(RES_XML_TYPE, 0);
  header.writeUInt16LE(8, 2);
  header.writeUInt32LE(header.length + body.length, 4);

  return Buffer.concat([header, body]);
}

// Walk the element tree and collect every unique string (element names,
// attribute names, and string attribute values) into an ordered array.
// The array position becomes the integer index used everywhere else.
function collectStrings(root: ElementInput): string[] {
  const values = new Set<string>();
  const visit = (element: ElementInput): void => {
    values.add(element.name);
    for (const attribute of element.attributes ?? []) {
      values.add(attribute.name);
      if (typeof attribute.value === "string") {
        values.add(attribute.value);
      }
    }
    for (const child of element.children ?? []) {
      visit(child);
    }
  };
  visit(root);
  return [...values];
}

// String pool chunk layout:
//   +0  type         (uint16) = RES_STRING_POOL_TYPE
//   +2  headerSize   (uint16) = 28
//   +4  chunkSize    (uint32)
//   +8  stringCount  (uint32)
//   +12 styleCount   (uint32) = 0 (no styles)
//   +16 flags        (uint32) = 0x100 = UTF-8 encoding
//   +20 stringsStart (uint32) offset from chunk start to string data
//   +24 stylesStart  (uint32) = 0
//   +28 offsets[]    uint32 per string, relative to stringsStart
//   +stringsStart: packed UTF-8 strings, each length-prefixed
function createStringPool(strings: string[]): Buffer {
  const encoded = strings.map(encodeUtf8String);
  const offsets: number[] = [];
  let dataLength = 0;
  for (const value of encoded) {
    offsets.push(dataLength);
    dataLength += value.length;
  }

  // Pad string data to a 4-byte boundary (required by the format).
  const padding = (4 - (dataLength % 4)) % 4;
  const headerSize = 28;
  const stringsStart = headerSize + strings.length * 4; // header + offset array
  const chunkSize = stringsStart + dataLength + padding;

  const chunk = Buffer.alloc(chunkSize);
  chunk.writeUInt16LE(RES_STRING_POOL_TYPE, 0);
  chunk.writeUInt16LE(headerSize, 2);
  chunk.writeUInt32LE(chunkSize, 4);
  chunk.writeUInt32LE(strings.length, 8);
  chunk.writeUInt32LE(0, 12);       // styleCount
  chunk.writeUInt32LE(0x100, 16);   // flags: UTF-8
  chunk.writeUInt32LE(stringsStart, 20);
  chunk.writeUInt32LE(0, 24);       // stylesStart

  offsets.forEach((offset, index) => chunk.writeUInt32LE(offset, headerSize + index * 4));

  let cursor = stringsStart;
  for (const value of encoded) {
    value.copy(chunk, cursor);
    cursor += value.length;
  }

  return chunk;
}

// Each UTF-8 string is stored as:
//   [character count — 1 or 2 bytes][byte count — 1 or 2 bytes][utf8 bytes][null terminator]
// The length encoding uses the high bit to signal a two-byte value (see encodeLength8).
function encodeUtf8String(value: string): Buffer {
  const encoded = Buffer.from(value, "utf8");
  const utf16Length = [...value].length; // character count (may differ from byte count for non-ASCII)
  return Buffer.concat([encodeLength8(utf16Length), encodeLength8(encoded.length), encoded, Buffer.from([0])]);
}

// Android's variable-length 1-or-2-byte encoding:
//   value < 128  → single byte
//   value ≥ 128  → two bytes: high bit set on first byte, value spread across both
function encodeLength8(value: number): Buffer {
  if (value < 0x80) {
    return Buffer.from([value]);
  }
  return Buffer.from([0x80 | ((value >> 8) & 0x7f), value & 0xff]);
}

// Recursively write start element + children + end element chunks for the whole tree.
function appendElementChunks(
  element: ElementInput,
  strings: Map<string, number>,
  chunks: Buffer[],
): void {
  chunks.push(createStartElement(element, strings));
  for (const child of element.children ?? []) {
    appendElementChunks(child, strings, chunks);
  }
  chunks.push(createEndElement(element.name, strings));
}

// Start element chunk layout:
//   +0  type           (uint16) = RES_XML_START_ELEMENT_TYPE
//   +2  headerSize     (uint16) = 16
//   +4  chunkSize      (uint32)
//   +8  lineNumber     (uint32) source line, unused here
//   +12 comment        (uint32) = NO_INDEX
//   +16 namespace      (uint32) = NO_INDEX (no namespace)
//   +20 name           (uint32) string index of element name
//   +24 attributeStart (uint16) = 20 (offset from +16 to first attribute)
//   +26 attributeSize  (uint16) = 20 (bytes per attribute)
//   +28 attributeCount (uint16)
//   +30 idIndex        (uint16) = 0
//   +32 classIndex     (uint16) = 0
//   +34 styleIndex     (uint16) = 0
//   +36 attributes[]   20 bytes each (see below)
function createStartElement(element: ElementInput, strings: Map<string, number>): Buffer {
  const attributes = element.attributes ?? [];
  const chunkSize = 36 + attributes.length * 20;
  const chunk = Buffer.alloc(chunkSize, 0xff);

  chunk.writeUInt16LE(RES_XML_START_ELEMENT_TYPE, 0);
  chunk.writeUInt16LE(16, 2);
  chunk.writeUInt32LE(chunkSize, 4);
  chunk.writeUInt32LE(1, 8);           // lineNumber
  chunk.writeUInt32LE(NO_INDEX, 12);   // comment
  chunk.writeUInt32LE(NO_INDEX, 16);   // namespace
  chunk.writeUInt32LE(indexOf(strings, element.name), 20);
  chunk.writeUInt16LE(20, 24);         // attributeStart
  chunk.writeUInt16LE(20, 26);         // attributeSize
  chunk.writeUInt16LE(attributes.length, 28);
  chunk.writeUInt16LE(0, 30);          // idIndex
  chunk.writeUInt16LE(0, 32);          // classIndex
  chunk.writeUInt16LE(0, 34);          // styleIndex

  // Each attribute is 20 bytes:
  //   +0  namespace  (uint32) = NO_INDEX
  //   +4  name       (uint32) string index
  //   +8  rawValue   (uint32) string index for human-readable value, or NO_INDEX
  //   +12 valueSize  (uint16) = 8
  //   +14 padding    (uint8)  = 0
  //   +15 dataType   (uint8)  TYPE_STRING / TYPE_INT_DEC / TYPE_INT_BOOLEAN
  //   +16 data       (uint32) typed value payload
  attributes.forEach((attribute, index) => {
    const offset = 36 + index * 20;
    chunk.writeUInt32LE(NO_INDEX, offset);                          // namespace
    chunk.writeUInt32LE(indexOf(strings, attribute.name), offset + 4);
    if (typeof attribute.value === "string") {
      const stringIndex = indexOf(strings, attribute.value);
      chunk.writeUInt32LE(stringIndex, offset + 8);                 // rawValue = same string
      chunk.writeUInt16LE(8, offset + 12);
      chunk.writeUInt8(0, offset + 14);
      chunk.writeUInt8(TYPE_STRING, offset + 15);
      chunk.writeUInt32LE(stringIndex, offset + 16);                // data = string index
    } else {
      chunk.writeUInt32LE(NO_INDEX, offset + 8);                    // no raw string for numbers/booleans
      chunk.writeUInt16LE(8, offset + 12);
      chunk.writeUInt8(0, offset + 14);
      chunk.writeUInt8(typeof attribute.value === "boolean" ? TYPE_INT_BOOLEAN : TYPE_INT_DEC, offset + 15);
      chunk.writeUInt32LE(
        typeof attribute.value === "boolean" ? (attribute.value ? 1 : 0) : attribute.value,
        offset + 16,
      );
    }
  });

  return chunk;
}

// End element chunk layout:
//   +0  type       (uint16) = RES_XML_END_ELEMENT_TYPE
//   +2  headerSize (uint16) = 16
//   +4  chunkSize  (uint32) = 24
//   +8  lineNumber (uint32)
//   +12 comment    (uint32) = NO_INDEX
//   +16 namespace  (uint32) = NO_INDEX
//   +20 name       (uint32) string index
function createEndElement(name: string, strings: Map<string, number>): Buffer {
  const chunk = Buffer.alloc(24, 0xff);
  chunk.writeUInt16LE(RES_XML_END_ELEMENT_TYPE, 0);
  chunk.writeUInt16LE(16, 2);
  chunk.writeUInt32LE(chunk.length, 4);
  chunk.writeUInt32LE(1, 8);
  chunk.writeUInt32LE(NO_INDEX, 12);
  chunk.writeUInt32LE(NO_INDEX, 16);
  chunk.writeUInt32LE(indexOf(strings, name), 20);
  return chunk;
}

function indexOf(strings: Map<string, number>, value: string): number {
  const index = strings.get(value);
  if (index === undefined) {
    throw new Error(`Missing test string: ${value}`);
  }
  return index;
}
