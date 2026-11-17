import json
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from .models import MAX_FILE, MAX_OUTPUT, VERSION, ConversionError


def convert_file(path: Path, source_name: str, encoding='auto'):
    size = path.stat().st_size
    if not size: raise ConversionError('empty_file', '文件为空。')
    if size > MAX_FILE: raise ConversionError('file_limit', '文件超过 20 MiB 限制。')
    data = path.read_bytes()
    suffix = Path(source_name).suffix.lower()
    if suffix == '.doc':
        raise ConversionError('legacy_word', '旧版 .doc 暂不支持，请在 Word/LibreOffice 中另存为 .docx。', skipped=True)
    if suffix == '.pdf':
        if not data.startswith(b'%PDF-'): raise ConversionError('format_mismatch', '扩展名与 PDF 格式不一致。')
        from .pdf import convert
        result = convert(data)
    elif suffix == '.docx':
        if not data.startswith(b'PK\x03\x04'): raise ConversionError('format_mismatch', '扩展名与 DOCX 格式不一致。')
        from .word import convert
        result = convert(data)
    elif suffix == '.txt':
        if data.startswith((b'%PDF-', b'PK\x03\x04', b'\xd0\xcf\x11\xe0')):
            raise ConversionError('format_mismatch', '文件是其他文档格式，不能作为 TXT 处理。')
        from .text import convert
        result = convert(data, encoding)
    else:
        raise ConversionError('unsupported_format', '不支持此文件格式。', skipped=True)
    if not result.body.strip(): raise ConversionError('empty_content', '未提取到有效内容，未生成空 Markdown。')
    result.title = result.title or Path(source_name).stem
    digest = sha256(data).hexdigest()
    fields = {'title': result.title, 'source_file': source_name, 'source_format': suffix[1:],
              'source_sha256': digest, 'converter_version': VERSION,
              'conversion_status': 'partial' if result.partial else 'success'}
    front = '---\n' + '\n'.join(f'{key}: {json.dumps(value, ensure_ascii=False)}' for key, value in fields.items()) + '\n---\n\n'
    # Offsets address the downloaded UTF-8 Markdown as Unicode code points.
    for item in result.mapping:
        item['start'] += len(front)
        item['end'] += len(front)
    markdown = front + result.body + '\n'
    if len(markdown.encode('utf-8')) > MAX_OUTPUT:
        raise ConversionError('output_limit', '转换结果超过 8 MiB 限制。')
    metadata = asdict(result)
    metadata.pop('body')
    return markdown, dict(metadata, digest=digest, converter_version=VERSION)


def process_file(input_path, output_path, source_name, encoding):
    """Spawn target: parsers run outside the API, with a killable per-file deadline."""
    output = Path(output_path)
    try:
        import logging
        import warnings
        logging.disable(logging.CRITICAL)
        warnings.simplefilter('ignore')
        from .resources import limit_memory
        try:
            limit_memory()
        except (OSError, ValueError):
            raise ConversionError('resource_limit_unavailable', '当前环境无法建立安全资源限制，未执行转换。') from None
        markdown, metadata = convert_file(Path(input_path), source_name, encoding)
        output.write_text(markdown, encoding='utf-8')
        output.with_suffix('.json').write_text(json.dumps({'ok': True, **metadata}, ensure_ascii=False), encoding='utf-8')
    except ConversionError as exc:
        try:
            output.with_suffix('.json').write_text(json.dumps({'ok': False, 'code': exc.code, 'message': exc.message, 'skipped': exc.skipped}), encoding='utf-8')
        except (OSError, MemoryError):
            pass  # Parent records converter_exit when even the safe error sidecar cannot be saved.
    except Exception as exc:
        # Never persist document contents, parser errors or absolute paths.
        code = 'resource_limit' if isinstance(exc, MemoryError) else 'storage_error' if isinstance(exc, OSError) else 'conversion_error'
        try:
            message = {'resource_limit': '转换内存达到上限，文件内容过于复杂。', 'storage_error': '存储空间不足或写入失败。'}.get(code, '文档转换失败，文件结构可能不受支持。')
            output.with_suffix('.json').write_text(json.dumps({'ok': False, 'code': code, 'message': message}), encoding='utf-8')
        except (OSError, MemoryError):
            pass
