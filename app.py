# -*- coding: utf-8 -*-
"""
文件工坊 - FileToolbox
一款多功能文件处理工具
"""
import os
import io
import zipfile
import tempfile
from flask import Flask, render_template, request, jsonify, send_file, session
from werkzeug.utils import secure_filename
from PIL import Image
import pandas as pd
import qrcode
from PyPDF2 import PdfMerger
import uuid
import json
import re
import socket
import logging
import webbrowser
import threading

# 检测是否在 PyInstaller 打包环境下运行
if getattr(sys, 'frozen', False):
    # --onedir 模式: sys._MEIPASS = _internal目录, sys.executable位置可写
    DATA_DIR = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    WRITE_DIR = os.path.dirname(sys.executable)
else:
    DATA_DIR = WRITE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__,
    template_folder=os.path.join(DATA_DIR, 'templates'),
    static_folder=os.path.join(DATA_DIR, 'static'))
app.secret_key = os.urandom(24).hex()
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max
app.config['UPLOAD_FOLDER'] = os.path.join(WRITE_DIR, 'uploads')
app.config['OUTPUT_FOLDER'] = os.path.join(WRITE_DIR, 'output')
app.config['SESSION_FILE'] = os.path.join(WRITE_DIR, '.session_key')

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)

ALLOWED_IMAGES = {'png', 'jpg', 'jpeg', 'webp', 'bmp', 'gif', 'tiff', 'ico'}
ALLOWED_PDFS = {'pdf'}
ALLOWED_TEXT = {'txt', 'csv', 'json', 'xml', 'md'}
ALLOWED_SPREADSHEETS = {'csv', 'xlsx', 'xls'}


def allowed_file(filename, extensions):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in extensions


def get_unique_filename(prefix='', ext=''):
    name = uuid.uuid4().hex[:12]
    return f"{prefix}_{name}.{ext}" if prefix else f"{name}.{ext}"


@app.route('/')
def index():
    return render_template('index.html')


# ===== 图片处理 =====

@app.route('/api/image/compress', methods=['POST'])
def image_compress():
    files = request.files.getlist('files')
    quality = int(request.form.get('quality', 70))
    if not files or files[0].filename == '':
        return jsonify({'error': '请上传图片文件'}), 400

    results = []
    for f in files:
        if not allowed_file(f.filename, ALLOWED_IMAGES):
            continue
        img = Image.open(f.stream)
        out_name = get_unique_filename('compressed', 'jpg')
        out_path = os.path.join(app.config['OUTPUT_FOLDER'], out_name)
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        img.save(out_path, 'JPEG', quality=quality, optimize=True)
        orig_size = len(f.read())
        f.stream.seek(0)
        new_size = os.path.getsize(out_path)
        results.append({
            'original': f.filename,
            'output': out_name,
            'original_size': orig_size,
            'new_size': new_size,
            'saved': f"{round((1 - new_size/orig_size) * 100, 1)}%"
        })

    if not results:
        return jsonify({'error': '没有可处理的图片文件'}), 400

    zip_name = get_unique_filename('compressed', 'zip')
    zip_path = os.path.join(app.config['OUTPUT_FOLDER'], zip_name)
    with zipfile.ZipFile(zip_path, 'w') as zf:
        for r in results:
            zf.write(os.path.join(app.config['OUTPUT_FOLDER'], r['output']), r['output'])
            os.remove(os.path.join(app.config['OUTPUT_FOLDER'], r['output']))

    return jsonify({
        'success': True,
        'download': zip_name,
        'results': results
    })


@app.route('/api/image/convert', methods=['POST'])
def image_convert():
    files = request.files.getlist('files')
    target_format = request.form.get('format', 'png').lower()
    if not files or files[0].filename == '':
        return jsonify({'error': '请上传图片文件'}), 400

    if target_format not in ['png', 'jpg', 'jpeg', 'webp', 'bmp', 'gif', 'ico']:
        return jsonify({'error': '不支持的输出格式'}), 400

    if target_format == 'jpg':
        target_format = 'jpeg'

    results = []
    for f in files:
        if not allowed_file(f.filename, ALLOWED_IMAGES):
            continue
        img = Image.open(f.stream)
        out_name = get_unique_filename('converted', target_format)
        out_path = os.path.join(app.config['OUTPUT_FOLDER'], out_name)
        if target_format in ('jpeg',) and img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        elif target_format in ('png', 'webp') and img.mode not in ('RGBA',):
            img = img.convert('RGBA')
        img.save(out_path, target_format.upper())
        results.append({
            'original': f.filename,
            'output': out_name,
            'format': target_format.upper()
        })

    if not results:
        return jsonify({'error': '没有可处理的图片文件'}), 400

    zip_name = get_unique_filename('converted', 'zip')
    zip_path = os.path.join(app.config['OUTPUT_FOLDER'], zip_name)
    with zipfile.ZipFile(zip_path, 'w') as zf:
        for r in results:
            zf.write(os.path.join(app.config['OUTPUT_FOLDER'], r['output']), r['output'])
            os.remove(os.path.join(app.config['OUTPUT_FOLDER'], r['output']))

    return jsonify({
        'success': True,
        'download': zip_name,
        'results': results
    })


@app.route('/api/image/resize', methods=['POST'])
def image_resize():
    files = request.files.getlist('files')
    width = int(request.form.get('width', 1920))
    height = int(request.form.get('height', 0))
    keep_ratio = request.form.get('keep_ratio', 'true') == 'true'

    if not files or files[0].filename == '':
        return jsonify({'error': '请上传图片文件'}), 400

    results = []
    for f in files:
        if not allowed_file(f.filename, ALLOWED_IMAGES):
            continue
        img = Image.open(f.stream)
        orig_w, orig_h = img.size

        if keep_ratio and height > 0:
            ratio = min(width/orig_w, height/orig_h)
            new_w, new_h = int(orig_w * ratio), int(orig_h * ratio)
        elif keep_ratio and width > 0:
            ratio = width / orig_w
            new_w, new_h = width, int(orig_h * ratio)
        elif height > 0:
            new_w, new_h = width, height
        else:
            new_w, new_h = width, int(orig_h * width / orig_w)

        img = img.resize((new_w, new_h), Image.LANCZOS)
        ext = f.filename.rsplit('.', 1)[1].lower()
        if ext in ('jpg', 'jpeg'):
            ext = 'jpg'
        out_name = get_unique_filename('resized', ext)
        out_path = os.path.join(app.config['OUTPUT_FOLDER'], out_name)
        save_kwargs = {'format': 'JPEG', 'quality': 92} if ext == 'jpg' else {}
        img.save(out_path, **save_kwargs)

        results.append({
            'original': f.filename,
            'output': out_name,
            'size': f"{orig_w}x{orig_h} → {new_w}x{new_h}"
        })

    if not results:
        return jsonify({'error': '没有可处理的图片文件'}), 400

    zip_name = get_unique_filename('resized', 'zip')
    zip_path = os.path.join(app.config['OUTPUT_FOLDER'], zip_name)
    with zipfile.ZipFile(zip_path, 'w') as zf:
        for r in results:
            zf.write(os.path.join(app.config['OUTPUT_FOLDER'], r['output']), r['output'])
            os.remove(os.path.join(app.config['OUTPUT_FOLDER'], r['output']))

    return jsonify({
        'success': True,
        'download': zip_name,
        'results': results
    })


# ===== PDF工具 =====

@app.route('/api/pdf/merge', methods=['POST'])
def pdf_merge():
    files = request.files.getlist('files')
    if not files or files[0].filename == '':
        return jsonify({'error': '请上传PDF文件'}), 400

    merger = PdfMerger()
    temp_files = []
    pdf_files = [f for f in files if allowed_file(f.filename, ALLOWED_PDFS)]

    if not pdf_files:
        return jsonify({'error': '没有可处理的PDF文件'}), 400

    for f in pdf_files:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        f.save(tmp.name)
        temp_files.append(tmp.name)
        merger.append(tmp.name)

    out_name = get_unique_filename('merged', 'pdf')
    out_path = os.path.join(app.config['OUTPUT_FOLDER'], out_name)
    merger.write(out_path)
    merger.close()

    for tf in temp_files:
        os.unlink(tf)

    return jsonify({
        'success': True,
        'download': out_name,
        'results': [{'original': f.filename} for f in pdf_files]
    })


@app.route('/api/pdf/split', methods=['POST'])
def pdf_split():
    file = request.files.get('file')
    if not file or file.filename == '':
        return jsonify({'error': '请上传一个PDF文件'}), 400

    if not allowed_file(file.filename, ALLOWED_PDFS):
        return jsonify({'error': '仅支持PDF格式'}), 400

    from PyPDF2 import PdfReader, PdfWriter
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
    file.save(tmp.name)

    reader = PdfReader(tmp.name)
    total = len(reader.pages)

    if total <= 1:
        os.unlink(tmp.name)
        return jsonify({'error': 'PDF只有1页，无需拆分'}), 400

    zip_name = get_unique_filename('split_pages', 'zip')
    zip_path = os.path.join(app.config['OUTPUT_FOLDER'], zip_name)

    with zipfile.ZipFile(zip_path, 'w') as zf:
        for i in range(total):
            writer = PdfWriter()
            writer.add_page(reader.pages[i])
            page_name = f"page_{i+1:03d}.pdf"
            page_path = os.path.join(app.config['OUTPUT_FOLDER'], page_name)
            with open(page_path, 'wb') as pf:
                writer.write(pf)
            zf.write(page_path, page_name)
            os.remove(page_path)

    os.unlink(tmp.name)

    return jsonify({
        'success': True,
        'download': zip_name,
        'total_pages': total
    })


# ===== 二维码生成 =====

@app.route('/api/qrcode/generate', methods=['POST'])
def qrcode_generate():
    data = request.form.get('data', '')
    size = int(request.form.get('size', 10))
    fg_color = request.form.get('fg_color', '#000000')
    bg_color = request.form.get('bg_color', '#FFFFFF')

    if not data:
        return jsonify({'error': '请输入要编码的内容'}), 400

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=size,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color=fg_color, back_color=bg_color).convert('RGB')
    out_name = get_unique_filename('qrcode', 'png')
    out_path = os.path.join(app.config['OUTPUT_FOLDER'], out_name)
    img.save(out_path, 'PNG')

    return jsonify({
        'success': True,
        'download': out_name,
        'preview': f"/download/{out_name}",
        'content': data
    })


@app.route('/api/qrcode/batch', methods=['POST'])
def qrcode_batch():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': '请上传包含内容的文件'}), 400

    content = file.read().decode('utf-8').strip().split('\n')
    content = [c.strip() for c in content if c.strip()]

    if not content:
        return jsonify({'error': '文件内容为空'}), 400

    total = len(content)
    max_items = min(total, 100)
    content = content[:max_items]

    zip_name = get_unique_filename('qrcodes', 'zip')
    zip_path = os.path.join(app.config['OUTPUT_FOLDER'], zip_name)

    with zipfile.ZipFile(zip_path, 'w') as zf:
        for i, line in enumerate(content):
            try:
                qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=2)
                qr.add_data(line)
                qr.make(fit=True)
                img = qr.make_image(fill_color='black', back_color='white').convert('RGB')
                qr_name = f"qrcode_{i+1:03d}.png"
                qr_path = os.path.join(app.config['OUTPUT_FOLDER'], qr_name)
                img.save(qr_path, 'PNG')
                zf.write(qr_path, qr_name)
                os.remove(qr_path)
            except:
                continue

    return jsonify({
        'success': True,
        'download': zip_name,
        'total': len(content)
    })


# ===== 文本工具 =====

@app.route('/api/text/dedup', methods=['POST'])
def text_dedup():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': '请上传文本文件'}), 400

    content = file.read().decode('utf-8').splitlines()
    lines = [l for l in content if l.strip()]
    unique_lines = list(dict.fromkeys(lines))

    result = '\n'.join(unique_lines)
    out_name = get_unique_filename('deduped', 'txt')
    out_path = os.path.join(app.config['OUTPUT_FOLDER'], out_name)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(result)

    return jsonify({
        'success': True,
        'download': out_name,
        'original_lines': len(lines),
        'unique_lines': len(unique_lines)
    })


@app.route('/api/text/sort', methods=['POST'])
def text_sort():
    file = request.files.get('file')
    order = request.form.get('order', 'asc')
    ignore_case = request.form.get('ignore_case', 'true') == 'true'

    if not file:
        return jsonify({'error': '请上传文本文件'}), 400

    content = file.read().decode('utf-8').splitlines()
    lines = [l for l in content if l.strip()]

    key_func = lambda x: x.lower() if ignore_case else lambda x: x
    lines.sort(key=key_func, reverse=(order == 'desc'))

    result = '\n'.join(lines)
    out_name = get_unique_filename('sorted', 'txt')
    out_path = os.path.join(app.config['OUTPUT_FOLDER'], out_name)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(result)

    return jsonify({
        'success': True,
        'download': out_name,
        'total_lines': len(lines)
    })


@app.route('/api/text/replace', methods=['POST'])
def text_replace():
    file = request.files.get('file')
    find = request.form.get('find', '')
    replace = request.form.get('replace', '')
    use_regex = request.form.get('use_regex', 'false') == 'true'

    if not file:
        return jsonify({'error': '请上传文本文件'}), 400
    if not find:
        return jsonify({'error': '请输入要查找的文本'}), 400

    content = file.read().decode('utf-8')

    if use_regex:
        new_content, count = re.subn(find, replace, content)
    else:
        new_content, count = content.replace(find, replace), content.count(find)

    out_name = get_unique_filename('replaced', 'txt')
    out_path = os.path.join(app.config['OUTPUT_FOLDER'], out_name)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

    return jsonify({
        'success': True,
        'download': out_name,
        'replacements': count
    })


# ===== 文件格式转换 =====

@app.route('/api/convert/csv2excel', methods=['POST'])
def csv_to_excel():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': '请上传CSV文件'}), 400
    if not allowed_file(file.filename, {'csv'}):
        return jsonify({'error': '仅支持CSV格式'}), 400

    df = pd.read_csv(file.stream)
    out_name = get_unique_filename('converted', 'xlsx')
    out_path = os.path.join(app.config['OUTPUT_FOLDER'], out_name)
    df.to_excel(out_path, index=False, engine='openpyxl')

    return jsonify({
        'success': True,
        'download': out_name,
        'rows': len(df),
        'columns': len(df.columns)
    })


@app.route('/api/convert/excel2csv', methods=['POST'])
def excel_to_csv():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': '请上传Excel文件'}), 400
    if not allowed_file(file.filename, {'xlsx', 'xls'}):
        return jsonify({'error': '仅支持xlsx/xls格式'}), 400

    df = pd.read_excel(file.stream)
    out_name = get_unique_filename('converted', 'csv')
    out_path = os.path.join(app.config['OUTPUT_FOLDER'], out_name)
    df.to_csv(out_path, index=False, encoding='utf-8-sig')

    return jsonify({
        'success': True,
        'download': out_name,
        'rows': len(df),
        'columns': len(df.columns)
    })


# ===== 文件下载 =====

@app.route('/download/<filename>')
def download_file(filename):
    safe_name = secure_filename(filename)
    file_path = os.path.join(app.config['OUTPUT_FOLDER'], safe_name)
    if not os.path.exists(file_path):
        return jsonify({'error': '文件不存在或已过期'}), 404
    return send_file(file_path, as_attachment=True, download_name=safe_name)


if __name__ == '__main__':
    prefer_port = int(os.environ.get('PORT', 5000))
    port = prefer_port
    for attempt in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) != 0:
                break
        port = prefer_port + attempt + 1
    if port != prefer_port:
        print(f"端口 {prefer_port} 已被占用，自动切换到端口 {port}")
    # 桌面本地工具，压制Flask开发服务器警告
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    print(f"文件工坊已启动！请访问: http://127.0.0.1:{port}")

    # 自动打开浏览器
    def _open_browser():
        import time
        time.sleep(1.5)
        webbrowser.open(f'http://127.0.0.1:{port}')
    threading.Thread(target=_open_browser, daemon=True).start()

    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)

