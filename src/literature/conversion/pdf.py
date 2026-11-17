"""Layout heuristics with explicit page provenance; no OCR or invented formulae."""
import re
from collections import Counter
from io import BytesIO
from statistics import median
import pdfplumber
from pypdf import PdfReader
from .models import Converted, ConversionError
from .word import table


def _lines(words):
    lines = []
    for word in sorted(words, key=lambda w: (round(w['top']/3), w['x0'])):
        if not lines or abs(lines[-1][0]['top'] - word['top']) > 3:
            lines.append([word])
        else:
            lines[-1].append(word)
    return lines


def _ordered(words, width):
    """Split two-column zones; full-width headings/tables delimit zones."""
    lines = _lines(words)
    # A genuine gutter has left/right text on several common rows and no crossing word.
    middle = width / 2
    dual = sum(any(w['x1'] < middle-8 for w in line) and any(w['x0'] > middle+8 for w in line)
               and not any(w['x0'] < middle+8 and w['x1'] > middle-8 for w in line) for line in lines)
    if dual < 3:
        return lines
    result, zone = [], []
    def flush():
        for left in (True, False):
            result.extend(_lines([w for row in zone for w in row if (w['x0'] < middle) == left]))
        zone.clear()
    for line in lines:
        if any(w['x0'] < middle+8 and w['x1'] > middle-8 for w in line):
            flush()
            result.append(line)
        else:
            zone.append(line)
    flush()
    return result


def convert(data):
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise ConversionError('encrypted_pdf', 'PDF 已加密，请先使用合法密码解密后再转换。')
        if len(reader.pages) > 200:
            raise ConversionError('page_limit', 'PDF 超过单文件 200 页限制。')
        with pdfplumber.open(BytesIO(data)) as doc:
            extracted = [p.extract_words(extra_attrs=['size']) for p in doc.pages]
            # Remove only repeated margin strings; retain body repetitions.
            margins = Counter()
            for page, words in zip(doc.pages, extracted):
                margins.update(set(' '.join(w['text'] for w in line) for line in _lines(words)
                                   if line[0]['top'] < 40 or line[0]['top'] > page.height-40))
            repeated = {s for s, count in margins.items() if count >= max(2, len(doc.pages)*.6)}
            sizes = [w['size'] for words in extracted for w in words]
            body_size = median(sizes) if sizes else 12
            pages, mapping, blocks, warnings = [], [], [], []
            partial, title = False, ''
            offset = 0
            for n, (page, words) in enumerate(zip(doc.pages, extracted), 1):
                tables = page.find_tables()
                if not words:
                    if page.images:
                        warnings.append(f'第 {n} 页为扫描图像，未启用 OCR。')
                        partial = True
                    else:
                        warnings.append(f'第 {n} 页无可提取文本。')
                    page_text = ''
                    rendered = '[本页无可提取文本，未进行 OCR]'
                else:
                    if page.images:
                        warnings.append(f'第 {n} 页含图片，仅提取文本层，图像内容未进行 OCR。')
                        area = sum(abs(image.get('width', 0)*image.get('height', 0)) for image in page.images)
                        if area > page.width * page.height * .2: partial = True
                    # Tables become source-ordered blocks; their words must not appear twice.
                    outside = [w for w in words if not any(t.bbox[0] <= (w['x0']+w['x1'])/2 <= t.bbox[2]
                               and t.bbox[1] <= w['top'] <= t.bbox[3] for t in tables)]
                    segments = []
                    # Each table is a layout boundary, keeping text above/below it in order.
                    boundaries = sorted(tables, key=lambda t: t.bbox[1])
                    lower = -1
                    for tab in [*boundaries, None]:
                        upper = tab.bbox[1] if tab else page.height+1
                        for line in _ordered([w for w in outside if lower <= w['top'] < upper], page.width):
                            text = ' '.join(w['text'] for w in sorted(line, key=lambda w: w['x0']))
                            top = line[0]['top']
                            margin = top < 40 or top > page.height-40
                            if margin and (text in repeated or re.fullmatch(r'\d+|Page\s+\d+', text, re.I)):
                                continue
                            size = median(w['size'] for w in line)
                            heading = ''
                            if size >= body_size*1.25 and len(text) < 200:
                                heading = '# ' if not title and n == 1 else '## '
                                if not title and n == 1: title = text
                            elif re.match(r'^\d+(?:\.\d+)*\.?\s+\D', text) and len(text) < 120:
                                level = min(6, 2 + text.split()[0].rstrip('.').count('.'))
                                heading = '#' * level + ' '
                            segments.append((heading + text, text))
                        if tab:
                            rendered_table = table(tab.extract())
                            segments.append((rendered_table, rendered_table))
                            lower = tab.bbox[3]
                    rendered = '\n\n'.join(a for a, _ in segments)
                    page_text = '\n\n'.join(b for _, b in segments)
                marker = f'<!-- page:{n} -->\n<a id="page-{n}"></a>\n\n'
                block = marker + rendered + '\n\n'
                mapping.append({'page': n, 'anchor': f'page-{n}', 'start': offset+len(marker), 'end': offset+len(marker)+len(rendered)})
                offset += len(block)
                pages.append(page_text)
                blocks.append(block)
            if not any(p.strip() for p in pages):
                raise ConversionError('scanned_pdf', '扫描文档或无文本 PDF，暂不支持完整解析；未启用 OCR。', skipped=True)
            warnings.append('PDF 标题与多栏顺序为版面启发式；复杂表格、插图和公式可能不完整。公式仅保留可提取文本，未推断 LaTeX。')
            return Converted(''.join(blocks).rstrip(), title, warnings, pages, mapping, partial=partial)
    except ConversionError:
        raise
    except MemoryError:
        raise
    except Exception:
        raise ConversionError('invalid_pdf', 'PDF 损坏或其版面无法解析。') from None
