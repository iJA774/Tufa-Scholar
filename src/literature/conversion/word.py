"""DOCX only. Never invoke Office, macros, embedded files or external relationships."""
import re
from collections import defaultdict
from io import BytesIO
from pathlib import PurePosixPath
from urllib.parse import urlsplit, quote
from zipfile import ZipFile, BadZipFile
from lxml import etree
from docx import Document
from docx.table import Table
from docx.text.hyperlink import Hyperlink
from .models import Converted, ConversionError

NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
      'm': 'http://schemas.openxmlformats.org/officeDocument/2006/math'}


def escape(text):
    return re.sub(r'([\\`*_<>{}\[\]#])', r'\\\1', text)


def table(rows):
    if not rows:
        return ''
    width = max(map(len, rows))
    rows = [list(row) + [''] * (width-len(row)) for row in rows]
    lines = ['| ' + ' | '.join(str(c or '').replace('\n', '<br>').replace('|', '\\|') for c in row) + ' |' for row in rows]
    return '\n'.join([lines[0], '| ' + ' | '.join(['---'] * width) + ' |', *lines[1:]])


def convert(data):
    try:
        with ZipFile(BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > 1000 or sum(m.file_size for m in members) > 100*1024*1024:
                raise ConversionError('container_limit', 'DOCX 解压内容超出安全限制。')
            for member in members:
                p = PurePosixPath(member.filename)
                if p.is_absolute() or '..' in p.parts or '\\' in member.filename or ':' in member.filename or member.flag_bits & 1:
                    raise ConversionError('unsafe_container', 'DOCX 包含不安全的容器条目。')
                if member.file_size > max(1024*1024, member.compress_size*200):
                    raise ConversionError('container_limit', 'DOCX 压缩比例超出安全限制。')
                if 'vbaproject' in member.filename.lower():
                    raise ConversionError('macro_document', '不处理含宏的文档。')
                if member.filename.endswith(('.xml', '.rels')):
                    xml = archive.read(member)
                    declarations = xml.upper().replace(b'\x00', b'')
                    if b'<!DOCTYPE' in declarations or b'<!ENTITY' in declarations:
                        raise ConversionError('unsafe_xml', '不处理包含实体声明的文档。')
            if 'word/document.xml' not in archive.namelist():
                raise ConversionError('invalid_docx', '文件不是有效的 DOCX 文档。')
            footnotes = archive.read('word/footnotes.xml') if 'word/footnotes.xml' in archive.namelist() else None
        document = Document(BytesIO(data))
    except (BadZipFile, KeyError, etree.XMLSyntaxError, ValueError):
        raise ConversionError('invalid_docx', 'DOCX 文件损坏或结构无效。') from None
    warnings = ['浮动对象、复杂编号、合并表格及公式版式可能无法完整还原；图片仅保留位置。']
    counters = defaultdict(int)
    title = ''
    partial = False

    def paragraph(p):
        nonlocal partial
        chunks = []
        for item in p.iter_inner_content():
            if isinstance(item, Hyperlink):
                label = escape(item.text)
                url = item.url
                # Inert Markdown only; disallow active protocols and avoid fetching the target.
                chunks.append(f'[{label}]({quote(url, safe=":/?=&%#@+;,$")})' if urlsplit(url).scheme in ('http', 'https', 'mailto') else label)
            else:
                value = escape(item.text)
                if item.bold and value.strip(): value = f'**{value}**'
                if item.italic and value.strip(): value = f'*{value}*'
                chunks.append(value)
        value = ''.join(chunks)
        for _ in p._p.xpath('.//w:drawing | .//w:pict'):
            value += ' [图片：未进行 OCR]'
            partial = True
        if p._p.xpath('.//w:object'):
            value += ' [嵌入对象：未执行或提取]'
            partial = True
        for reference in p._p.xpath('.//w:footnoteReference'):
            value += f'[^note-{reference.get("{"+NS["w"]+"}id")}]'
        for math in p._p.xpath('.//m:oMath'):
            literal = ''.join(math.itertext())
            # OMML order is source order, not invented LaTeX.
            value += f'\n\n[公式原始文本，结构未还原：{escape(literal)}]'
            partial = True
        style = p.style.name if p.style else ''
        heading = re.search(r'Heading\s*(\d+)', style, re.I)
        if heading: return '#' * min(6, int(heading[1])) + ' ' + value
        if style == 'Title': return '# ' + value
        num = p._p.xpath('./w:pPr/w:numPr')
        if num or style.startswith('List'):
            level = p._p.xpath('./w:pPr/w:numPr/w:ilvl/@w:val')
            depth = min(8, int(level[0])) if level else 0
            num_id = p._p.xpath('./w:pPr/w:numPr/w:numId/@w:val')
            ordered = 'Number' in style
            if num_id:
                numbering = document.part.numbering_part.element
                abstract = numbering.xpath(f'./w:num[@w:numId="{int(num_id[0])}"]/w:abstractNumId/@w:val')
                if abstract:
                    fmt = numbering.xpath(f'./w:abstractNum[@w:abstractNumId="{int(abstract[0])}"]/w:lvl[@w:ilvl="{depth}"]/w:numFmt/@w:val')
                    ordered = bool(fmt and fmt[0] != 'bullet')
            key = (tuple(num_id) or (style,), depth)
            counters[key] += 1
            return '  ' * depth + (f'{counters[key]}. ' if ordered else '- ') + value
        return value

    blocks = []
    for block in document.iter_inner_content():
        if isinstance(block, Table):
            rows = []
            for row in block.rows:
                cells = []
                for cell in row.cells:
                    value = '\n'.join(paragraph(p) for p in cell.paragraphs)
                    if cell.tables:
                        value += '\n[嵌套表格：未完整展开]'
                        partial = True
                    cells.append(value)
                rows.append(cells)
            blocks.append(table(rows))
        else:
            value = paragraph(block)
            if not title and value.startswith('# '): title = block.text.strip()
            if value.strip(): blocks.append(value)
    if footnotes:
        root = etree.fromstring(footnotes, parser=etree.XMLParser(resolve_entities=False, no_network=True))
        for note in root.findall('w:footnote', NS):
            ident = note.get('{'+NS['w']+'}id')
            if ident and int(ident) > 0:
                blocks.append(f'[^note-{ident}]: ' + escape(' '.join(note.xpath('.//w:t/text()', namespaces=NS))))
    return Converted('\n\n'.join(blocks), title, warnings, partial=partial)
