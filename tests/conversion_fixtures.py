"""Small original synthetic documents, generated identically for unit/API/UI checks."""
import io
from pathlib import Path
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject, NumberObject
from docx import Document
from docx.shared import Inches
from PIL import Image


def pdf_fixture(kind='ordinary'):
    writer = PdfWriter()
    for n in range(2 if kind == 'ordinary' else 1):
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        ops = []
        def line(text, x, y, size=12):
            ops.append(f'BT /F1 {size} Tf {x} {y} Td ({text}) Tj ET')
        if kind == 'ordinary':
            line('Repeated running header', 40, 770)
            line('Synthetic Research Title' if n == 0 else '2 Results', 50, 710, 22 if n == 0 else 16)
            line('1 Introduction' if n == 0 else 'Evidence on second page', 50, 665, 16 if n == 0 else 12)
            line('Traceable quantum evidence for testing.', 50, 625)
            line(str(n+1), 300, 20)
        elif kind == 'columns':
            line('Two Column Study', 220, 730, 20)
            for i in range(5):
                line(f'LEFT_{i} source text.', 45, 680-i*22)
                line(f'RIGHT_{i} source text.', 330, 680-i*22)
        elif kind == 'table':
            line('Data Table', 50, 720, 20)
            for y in (600, 630, 660): ops.append(f'50 {y} m 400 {y} l S')
            for x in (50, 220, 400): ops.append(f'{x} 600 m {x} 660 l S')
            for text, x, y in [('Method',60,640),('Score',230,640),('Baseline',60,610),('0.95',230,610)]: line(text,x,y)
        elif kind == 'math':
            line('1 Formula',50,700,18)
            line('E = mc^2',50,660)
            line('References',50,610,16)
            line('Original source citation retained.',50,570)
        elif kind == 'scan':
            image = DictionaryObject({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'), NameObject('/Width'): NumberObject(1), NameObject('/Height'): NumberObject(1), NameObject('/ColorSpace'): NameObject('/DeviceRGB'), NameObject('/BitsPerComponent'): NumberObject(8)})
            stream = DecodedStreamObject(); stream.update(image); stream.set_data(b'\x00\x00\x00')
            page['/Resources'][NameObject('/XObject')] = DictionaryObject({NameObject('/Im1'): writer._add_object(stream)})
            ops.append('q 400 0 0 600 40 40 cm /Im1 Do Q')
        content = DecodedStreamObject(); content.set_data('\n'.join(ops).encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(content)
    if kind == 'encrypted': writer.encrypt('secret')
    out = io.BytesIO(); writer.write(out); return out.getvalue()


def docx_fixture(images=False):
    doc = Document()
    doc.add_heading('Synthetic Word Title', 0)
    doc.add_heading('Methods', 1)
    p = doc.add_paragraph('Natural paragraph ')
    p.add_run('bold').bold = True
    p.add_run(' and ')
    p.add_run('italic').italic = True
    doc.add_paragraph('Bullet point', style='List Bullet')
    doc.add_paragraph('First numbered', style='List Number')
    doc.add_paragraph('Second numbered', style='List Number')
    table = doc.add_table(rows=2, cols=2)
    for row, values in zip(table.rows, [('Name','Value'),('alpha','42')]):
        for cell, text in zip(row.cells, values): cell.text = text
    if images:
        picture = io.BytesIO(); Image.new('RGB',(8,8),'blue').save(picture, format='PNG'); picture.seek(0)
        doc.add_picture(picture, width=Inches(.5))
    doc.add_paragraph('Last paragraph after table.')
    out = io.BytesIO(); doc.save(out); return out.getvalue()


def write_fixtures(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for kind in ('ordinary','columns','table','math','scan','encrypted'):
        (directory / f'{kind}.pdf').write_bytes(pdf_fixture(kind))
    (directory/'structured.docx').write_bytes(docx_fixture())
    (directory/'image.docx').write_bytes(docx_fixture(True))
    (directory/'notes.txt').write_text('Plain text\n\nSecond paragraph.\n', encoding='utf-8-sig')
    (directory/'gbk.txt').write_bytes('本地科研文档，编码测试。这里保留原文，不生成标题。'.encode('gb18030'))
    (directory/'damaged.pdf').write_bytes(b'%PDF- broken')
    (directory/'legacy.doc').write_bytes(b'\xd0\xcf\x11\xe0unsupported')
    sub = directory/'DeepLearning'; sub.mkdir(exist_ok=True)
    (sub/'notes.txt').write_text('Nested document evidence.', encoding='utf-8')
