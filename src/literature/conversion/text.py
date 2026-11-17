import re
from charset_normalizer import from_bytes
from .models import Converted, ConversionError


def convert(data: bytes, encoding='auto'):
    detected = encoding
    if encoding == 'auto':
        if data.startswith((b'\xff\xfe', b'\xfe\xff')):
            detected = 'utf-16'
        else:
            try:
                data.decode('utf-8-sig')
                detected = 'utf-8-sig'
            except UnicodeDecodeError:
                match = from_bytes(data).best()
                if not match or match.chaos > .1 or (match.coherence < .2 and not match.encoding.startswith('gb')):
                    raise ConversionError('encoding_unknown', '无法可靠识别编码，请选择原文件编码后重试。')
                detected = match.encoding
    try:
        text = data.decode(detected, errors='strict')
    except (UnicodeError, LookupError):
        raise ConversionError('encoding_invalid', '文件与所选编码不匹配。') from None
    if '\x00' in text or any(ord(c) < 32 and c not in '\n\r\t\f' for c in text):
        raise ConversionError('invalid_text', '文件包含二进制控制字符，无法作为 TXT 转换。')
    text = re.sub(r'\n{3,}', '\n\n', text.replace('\r\n', '\n').replace('\r', '\n')).strip()
    # Plain text remains plain text; leading Markdown syntax is escaped, not promoted to headings.
    text = re.sub(r'(?m)^(\s*)([#>+*\-]|\d+[.)])(?=\s)', r'\1\\\2', text)
    warnings = [f'编码由启发式识别为 {detected}；如有乱码，请选择原文件编码重试。'] if encoding == 'auto' and detected not in ('utf-8-sig','utf-16') else []
    return Converted(text, encoding=detected, warnings=warnings)
