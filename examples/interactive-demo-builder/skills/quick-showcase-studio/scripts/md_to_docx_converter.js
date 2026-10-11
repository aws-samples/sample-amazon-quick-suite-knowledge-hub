/**
 * md_to_docx_converter.js
 * 
 * Prepackaged Markdown-to-DOCX converter for demo script generation.
 * Bundled with the quick-showcase-studio skill.
 * 
 * Usage:
 *   Set session.MD_INPUT_PATH and session.DOCX_OUTPUT_PATH before running.
 *   e.g.:
 *     session.MD_INPUT_PATH = '/path/to/2)demo-script.md';
 *     session.DOCX_OUTPUT_PATH = WORKSPACE_DIR + '/artifacts/3)demo-script.docx';
 * 
 * Handles: H1-H3 headings, bold/italic, bullet lists, horizontal rules, 
 *          code blocks, blockquotes, and paragraphs.
 */

const fs = require('fs');
const docx = require('docx');

const {
  Document, Packer, Paragraph, TextRun, HeadingLevel,
  AlignmentType, BorderStyle, TabStopPosition, TabStopType,
  Table, TableRow, TableCell, WidthType
} = docx;

// Read input
const mdPath = session.MD_INPUT_PATH;
const outputPath = session.DOCX_OUTPUT_PATH;

if (!mdPath || !outputPath) {
  throw new Error('session.MD_INPUT_PATH and session.DOCX_OUTPUT_PATH must be set');
}

const mdContent = fs.readFileSync(mdPath, 'utf-8');
const lines = mdContent.split('\n');

// Parse markdown into document paragraphs
const children = [];
let inCodeBlock = false;
let codeBlockLines = [];

function parseInlineFormatting(text) {
  // Parse bold (**text** or __text__) and italic (*text* or _text_)
  const runs = [];
  // Regex to split on bold/italic markers
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g);
  
  for (const part of parts) {
    if (part.startsWith('**') && part.endsWith('**')) {
      runs.push(new TextRun({ text: part.slice(2, -2), bold: true }));
    } else if (part.startsWith('*') && part.endsWith('*') && part.length > 2) {
      runs.push(new TextRun({ text: part.slice(1, -1), italics: true }));
    } else if (part) {
      runs.push(new TextRun({ text: part }));
    }
  }
  return runs.length > 0 ? runs : [new TextRun({ text })];
}

// --- Markdown table support -------------------------------------------------
// Split a "| a | b |" row into trimmed cell strings (leading/trailing pipes optional).
function parseTableRow(line) {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
}

// A separator row is the "| --- | :--: |" line under the header: every cell is
// dashes with optional leading/trailing colons for alignment.
function isTableSeparator(line) {
  if (!line || line.indexOf('|') === -1) return false;
  const cells = parseTableRow(line);
  return cells.length > 0 && cells.every(c => /^:?-{2,}:?$/.test(c));
}

const TABLE_BORDER = { style: BorderStyle.SINGLE, size: 1, color: 'BFBFBF' };

function buildTable(headerCells, bodyRows) {
  const width = headerCells.length;
  const pad = (cells) => {
    const out = cells.slice(0, width);
    while (out.length < width) out.push('');
    return out;
  };
  const makeCell = (text, isHeader) => new TableCell({
    margins: { top: 40, bottom: 40, left: 100, right: 100 },
    children: [new Paragraph({
      children: isHeader
        ? [new TextRun({ text, bold: true })]
        : parseInlineFormatting(text),
    })],
  });
  const rows = [];
  rows.push(new TableRow({
    tableHeader: true,
    children: headerCells.map(c => makeCell(c, true)),
  }));
  for (const r of bodyRows) {
    rows.push(new TableRow({ children: pad(r).map(c => makeCell(c, false)) }));
  }
  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    borders: {
      top: TABLE_BORDER, bottom: TABLE_BORDER, left: TABLE_BORDER, right: TABLE_BORDER,
      insideHorizontal: TABLE_BORDER, insideVertical: TABLE_BORDER,
    },
    rows,
  });
}

for (let i = 0; i < lines.length; i++) {
  const line = lines[i];
  
  // Code block handling
  if (line.startsWith('```')) {
    if (inCodeBlock) {
      // End code block - emit accumulated lines
      children.push(new Paragraph({
        children: [new TextRun({ 
          text: codeBlockLines.join('\n'), 
          font: 'Courier New',
          size: 20 // 10pt
        })],
        spacing: { before: 100, after: 100 },
      }));
      codeBlockLines = [];
      inCodeBlock = false;
    } else {
      inCodeBlock = true;
    }
    continue;
  }
  
  if (inCodeBlock) {
    codeBlockLines.push(line);
    continue;
  }
  
  // Table: a header row containing pipes immediately followed by a separator row.
  if (line.indexOf('|') !== -1 && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
    const headerCells = parseTableRow(line);
    const bodyRows = [];
    let j = i + 2; // skip header (i) and separator (i+1)
    while (j < lines.length && lines[j].trim().indexOf('|') !== -1 && lines[j].trim() !== '') {
      bodyRows.push(parseTableRow(lines[j]));
      j++;
    }
    children.push(buildTable(headerCells, bodyRows));
    // A table sits tight against surrounding text; add a little breathing room.
    children.push(new Paragraph({ children: [], spacing: { before: 60, after: 60 } }));
    i = j - 1; // resume after the last consumed table line
    continue;
  }
  
  // Horizontal rule
  if (line.match(/^---+$/)) {
    children.push(new Paragraph({
      children: [],
      border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: 'CCCCCC' } },
      spacing: { before: 200, after: 200 },
    }));
    continue;
  }
  
  // Headings
  if (line.startsWith('# ') && !line.startsWith('## ')) {
    children.push(new Paragraph({
      children: parseInlineFormatting(line.slice(2)),
      heading: HeadingLevel.HEADING_1,
      spacing: { before: 240, after: 120 },
    }));
    continue;
  }
  if (line.startsWith('## ') && !line.startsWith('### ')) {
    children.push(new Paragraph({
      children: parseInlineFormatting(line.slice(3)),
      heading: HeadingLevel.HEADING_2,
      spacing: { before: 200, after: 100 },
    }));
    continue;
  }
  if (line.startsWith('### ')) {
    children.push(new Paragraph({
      children: parseInlineFormatting(line.slice(4)),
      heading: HeadingLevel.HEADING_3,
      spacing: { before: 160, after: 80 },
    }));
    continue;
  }
  
  // Blockquote
  if (line.startsWith('> ')) {
    children.push(new Paragraph({
      children: parseInlineFormatting(line.slice(2)),
      indent: { left: 720 }, // 0.5 inch
      spacing: { before: 60, after: 60 },
    }));
    continue;
  }
  
  // Bullet list (- or *)
  if (line.match(/^\s*[-*]\s+/)) {
    const indent = line.match(/^(\s*)/)[1].length;
    const text = line.replace(/^\s*[-*]\s+/, '');
    children.push(new Paragraph({
      children: parseInlineFormatting(text),
      bullet: { level: Math.min(Math.floor(indent / 2), 3) },
      spacing: { before: 40, after: 40 },
    }));
    continue;
  }
  
  // Numbered list
  if (line.match(/^\s*\d+\.\s+/)) {
    const text = line.replace(/^\s*\d+\.\s+/, '');
    children.push(new Paragraph({
      children: parseInlineFormatting(text),
      bullet: { level: 0 },
      spacing: { before: 40, after: 40 },
    }));
    continue;
  }
  
  // Empty line
  if (line.trim() === '') {
    children.push(new Paragraph({ children: [], spacing: { before: 60, after: 60 } }));
    continue;
  }
  
  // Regular paragraph
  children.push(new Paragraph({
    children: parseInlineFormatting(line),
    spacing: { before: 60, after: 60 },
  }));
}

// Build document
const doc = new Document({
  sections: [{
    properties: {},
    children: children,
  }],
});

// Write output
Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync(outputPath, buffer);
  console.log(`\u2705 DOCX generated successfully: ${outputPath}`);
  console.log(`   Size: ${buffer.length} bytes`);
  console.log(`   Paragraphs: ${children.length}`);
}).catch(err => {
  console.error('ERROR generating DOCX:', err.message);
});
