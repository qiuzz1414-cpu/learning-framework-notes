#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验一本已生成的「学习框架笔记」教材，或校验本 skill 包自身。

用法：
    python3 scripts/check_book.py <教材目录>      # 校验产出
    python3 scripts/check_book.py --self           # 校验 skill 包自身

退出码：0 全部通过；1 存在 ERROR；2 用法错误。
WARN 不改变退出码，但会打印并计入报告。

设计约束：只用标准库，不依赖网络，不做任何写入。
"""

import os
import re
import sys

# ---------------------------------------------------------------- 通用工具

ID_PAT = re.compile(r'ch(\d+)-pat-(\d+)')
ID_EX = re.compile(r'ch(\d+)-ex-(\d{3})')
ID_SEC = re.compile(r'ch(\d+)-sec-(\d+)')

# 定义：套路出现在标题里；题目出现在行首（可带列表符号）的反引号块内
# 兼容两种写法：`ch1-ex-001` 与 `ch1-ex-001　[a.pdf·2023]`（整段在反引号内）
DEF_PAT = re.compile(r'^\s*#{2,6}\s+`?ch(\d+)-pat-(\d+)`?')
DEF_EX = re.compile(r'^\s*(?:[-*]\s+)?`ch(\d+)-ex-(\d{3})(?=[`　\s])')

GAP_TYPES = {'素材未覆盖', '答案缺失', '素材冲突存疑', '页码不明'}

# 模板占位符：成书模式产物里不该残留（{公式占位} 等多学科变体也要覆盖）
PLACEHOLDERS = ['{主题}', '{填定值}', '{章 1 主题}', '{章 2 主题}',
                '{公式占位}', '{关键史实/判据}', '{关键 API/复杂度}',
                '{章 A}', '{章 B}', '{章 C}', '{章 D}', '{套路名}', '{题面}',
                '{按学科列出不许跳的步骤类型}']


class Report:
    def __init__(self):
        self.errors = []
        self.warns = []

    def error(self, rule, where, msg):
        self.errors.append((rule, where, msg))

    def warn(self, rule, where, msg):
        self.warns.append((rule, where, msg))

    def dump(self, title):
        print(f'\n=== {title} ===')
        if not self.errors and not self.warns:
            print('✅ 全部检查通过')
            return
        for rule, where, msg in self.errors:
            print(f'❌ [{rule}] {where}: {msg}')
        for rule, where, msg in self.warns:
            print(f'⚠️  [{rule}] {where}: {msg}')
        print(f'\n合计：{len(self.errors)} 个 ERROR，{len(self.warns)} 个 WARN')


def read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def md_files(root):
    for base, _dirs, files in os.walk(root):
        if '.git' in base:
            continue
        for name in sorted(files):
            if name.endswith('.md'):
                yield os.path.join(base, name)


def rel(path, root):
    return os.path.relpath(path, root)


def table_blocks(text):
    """产出 (起始行号, [行, ...]) 的表格块。"""
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith('|'):
            block = []
            j = i
            while j < len(lines) and lines[j].strip().startswith('|'):
                block.append((j + 1, lines[j]))
                j += 1
            yield block
            i = j
        else:
            i += 1


def exempt_line_numbers(text):
    """返回不应做「引用可解析」校验的行号集合。

    两处例外：
    1. 缺口清单的「位置」列——缺口本就指向缺失内容，引用必然无法解析
    2. 元数据表的「题号」列——它是登记项，不是正文引用
    """
    exempt = set()
    for block in table_blocks(text):
        header = [c.strip() for c in block[0][1].strip().strip('|').split('|')]
        if not header:
            continue
        if '缺口类型' in header:
            for lineno, _row in block[2:]:
                exempt.add(lineno)
        elif header[0] == '题号':
            for lineno, _row in block[2:]:
                exempt.add(lineno)
    return exempt


def cols(row):
    """表格行的列数（忽略被转义的竖线）。"""
    return len(re.findall(r'(?<!\\)\|', row)) - 1


# ---------------------------------------------------------------- 教材校验

def check_book(root, rep):
    chapters_dir = os.path.join(root, 'chapters')
    if not os.path.isdir(chapters_dir):
        rep.error('B00', root, '缺少 chapters/ 目录')
        return

    # 扫描范围：排除规范模板与章模板（它们含示例 ID，不是真实产物）
    scan_files = []
    for path in md_files(root):
        name = os.path.basename(path)
        if name in ('00-编写规范.md', '02-章模板.md'):
            continue
        scan_files.append(path)

    defs_pat, defs_ex, defs_sec = {}, {}, {}
    all_refs = []          # (kind, id, file, lineno)
    file_texts = {}

    for path in scan_files:
        text = read(path)
        file_texts[path] = text
        exempt = exempt_line_numbers(text)
        for lineno, line in enumerate(text.split('\n'), 1):
            m = DEF_PAT.match(line)
            if m:
                cid = f'ch{m.group(1)}-pat-{m.group(2)}'
                defs_pat.setdefault(cid, []).append((path, lineno))
                # 定义行只登记定义，该行剩余内容仍可含其他引用
                residue = DEF_PAT.sub('', line, count=1)
            else:
                m = DEF_EX.match(line)
                if m:
                    cid = f'ch{m.group(1)}-ex-{m.group(2)}'
                    defs_ex.setdefault(cid, []).append((path, lineno))
                    residue = DEF_EX.sub('', line, count=1)
                else:
                    residue = line
            if lineno in exempt:
                continue
            for kind, regex in (('pat', ID_PAT), ('ex', ID_EX), ('sec', ID_SEC)):
                for mm in regex.finditer(residue):
                    cid = f'ch{mm.group(1)}-{kind}-{mm.group(2)}'
                    all_refs.append((kind, cid, path, lineno))

    # B04 ID 唯一
    for label, table in (('套路', defs_pat), ('题目', defs_ex)):
        for cid, places in table.items():
            if len(places) > 1:
                where = ', '.join(f'{rel(p, root)}:{n}' for p, n in places)
                rep.error('B04', cid, f'{label} ID 重复定义于 {where}')

    # B05 引用可解析
    known = {'pat': set(defs_pat), 'ex': set(defs_ex), 'sec': set(defs_sec)}
    for kind, cid, path, lineno in all_refs:
        if cid not in known[kind]:
            rep.error('B05', f'{rel(path, root)}:{lineno}',
                      f'引用了不存在的 {cid}')

    # B02/B03 章节文件与 N
    skel_path = os.path.join(root, '01-全书骨架.md')
    n_value = None
    if os.path.exists(skel_path):
        m = re.search(r'N\s*=\s*(\d+)', read(skel_path))
        if m:
            n_value = int(m.group(1))
        else:
            rep.error('B01', '01-全书骨架.md', '未找到 "N = <数字>"，章号无法校验')
    else:
        rep.error('B02', root, '缺少 01-全书骨架.md')

    chapter_files = sorted(os.listdir(chapters_dir)) if os.path.isdir(chapters_dir) else []
    chapter_nums = sorted(int(m.group(1))
                          for f in chapter_files
                          for m in [re.match(r'ch(\d+)-', f)] if m)
    if n_value is not None:
        missing = [i for i in range(1, n_value + 1) if i not in chapter_nums]
        if missing:
            rep.error('B02', 'chapters/', f'缺少章文件：ch{missing}（N={n_value}）')
        extra = [i for i in chapter_nums if i > n_value]
        if extra:
            rep.error('B03', 'chapters/', f'章号超出 N={n_value}：ch{extra}')

    # B06 聚合章不新增 ID
    for path in scan_files:
        base = os.path.basename(path)
        if base.startswith('ch') and not path.startswith(chapters_dir):
            text = file_texts[path]
            for lineno, line in enumerate(text.split('\n'), 1):
                if DEF_PAT.match(line) or DEF_EX.match(line):
                    rep.error('B06', f'{rel(path, root)}:{lineno}',
                              '聚合章/附录不得定义新的套路或题目 ID')

    # B15 套路归属章正确
    for cid, places in defs_pat.items():
        chap = int(re.search(r'ch(\d+)', cid).group(1))
        for path, lineno in places:
            if os.path.dirname(path) == chapters_dir:
                m = re.match(r'ch(\d+)-', os.path.basename(path))
                if m and int(m.group(1)) != chap:
                    rep.error('B15', f'{rel(path, root)}:{lineno}',
                              f'{cid} 定义在 ch{m.group(1)} 文件内')

    # B07 每题有答案
    for path in scan_files:
        if not path.startswith(chapters_dir):
            continue
        text = file_texts[path]
        lines = text.split('\n')
        positions = [(i, DEF_EX.match(l).group(0))
                     for i, l in enumerate(lines) if DEF_EX.match(l)]
        for idx, (lineno, _label) in enumerate(positions):
            end = positions[idx + 1][0] if idx + 1 < len(positions) else len(lines)
            body = '\n'.join(lines[lineno:end])
            if '**答案**' not in body and '答案见' not in body and '待补' not in body:
                rep.error('B07', f'{rel(path, root)}:{lineno + 1}',
                          '题目缺少 **答案** 块（无答案时应写 {待补}）')

    # B08/B09 元数据表
    meta_path = skel_path
    if os.path.exists(meta_path):
        meta_text = read(meta_path)
        meta_ids = set()
        for block in table_blocks(meta_text):
            header = [c.strip() for c in block[0][1].strip().strip('|').split('|')]
            if header and header[0] == '题号':
                width = cols(block[0][1])
                for lineno, row in block[2:]:
                    if set(row.replace('|', '').strip()) <= {'-', ':', ' '}:
                        continue
                    if cols(row) != width:
                        rep.error('B09', f'{rel(meta_path, root)}:{lineno}',
                                  f'列数 {cols(row)} 与表头 {width} 不一致')
                    cells = [c.strip() for c in row.strip().strip('|').split('|')]
                    if cells and cells[0]:
                        meta_ids.add(cells[0])
        for cid in defs_ex:
            if cid not in meta_ids:
                rep.error('B08', cid, '题目未登记进元数据表')
        for cid in meta_ids:
            if cid not in defs_ex and not cid.startswith('ch{'):
                rep.error('B08', cid, '元数据表登记了不存在的题目')

    # B10 缺口类型
    for path in scan_files:
        text = file_texts[path]
        for lineno, line in enumerate(text.split('\n'), 1):
            m = re.search(r'缺口类型', line)
            if m:
                continue
            for cell in re.findall(r'\|\s*([^|]+?)\s*\|', line):
                pass  # 逐行扫描表格在下面统一做
        for block in table_blocks(text):
            header = [c.strip() for c in block[0][1].strip().strip('|').split('|')]
            if '缺口类型' not in header:
                continue
            idx = header.index('缺口类型')
            for lineno, row in block[2:]:
                cells = [c.strip() for c in row.strip().strip('|').split('|')]
                if len(cells) > idx and cells[idx] and cells[idx] not in GAP_TYPES:
                    rep.error('B10', f'{rel(path, root)}:{lineno}',
                              f'缺口类型 "{cells[idx]}" 不在允许集合内')

    # B11 占位符残留
    for path in scan_files:
        text = file_texts[path]
        for needle in PLACEHOLDERS:
            if needle in text:
                lineno = text[:text.index(needle)].count('\n') + 1
                rep.warn('B11', f'{rel(path, root)}:{lineno}',
                         f'成书产物中残留模板占位符 {needle}')

    # B12 套路七步完整（第 4 步兼容旧名「形式化解答」，新名为「示范解答」）
    for path in scan_files:
        if not path.startswith(chapters_dir):
            continue
        lines = file_texts[path].split('\n')
        starts = [(i, DEF_PAT.match(l).group(0))
                  for i, l in enumerate(lines) if DEF_PAT.match(l)]
        for idx, (lineno, _label) in enumerate(starts):
            end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
            body = '\n'.join(lines[lineno:end])
            missing = []
            for step in ('何时使用', '典型例题', '思路推演', '套路提炼', '关联编号'):
                if step not in body:
                    missing.append(step)
            # 易错点与示范解答需匹配加粗步骤标记，避免被「易错点X」这类畸形写法蒙混
            if '**易错点**' not in body:
                missing.append('易错点')
            if '**示范解答**' not in body and '**形式化解答**' not in body:
                missing.append('示范解答')
            if missing:
                rep.error('B12', f'{rel(path, root)}:{lineno + 1}',
                          f'套路缺少步骤：{missing}')

    # B13 表格列数一致
    for path in scan_files:
        for block in table_blocks(file_texts[path]):
            widths = {cols(r) for _n, r in block}
            if len(widths) > 1:
                rep.error('B13', f'{rel(path, root)}:{block[0][0]}',
                          f'表格列数不一致：{sorted(widths)}')

    # B14 题号三位（直接扫文本，不依赖定义行是否被识别）
    for path in scan_files:
        for lineno, line in enumerate(file_texts[path].split('\n'), 1):
            for mm in re.finditer(r'ch\d+-ex-(\d+)(?![0-9])', line):
                if len(mm.group(1)) != 3:
                    rep.error('B14', f'{rel(path, root)}:{lineno}',
                              f'题号 {mm.group(0)} 须为三位补零（chX-ex-001）')


# ---------------------------------------------------------------- 自校验

def check_self(root, rep):
    required = ['SKILL.md', 'README.md', 'LICENSE',
                'assets/全书骨架.md', 'assets/章模板.md', 'assets/编写规范.md',
                'references/成书流程.md']
    for name in required:
        if not os.path.exists(os.path.join(root, name)):
            rep.error('S01', name, '必需文件缺失')

    md_texts = {p: read(p) for p in md_files(root)}

    # S02 反引号内的 .md 路径必须存在（仅包内文件；产物文件名不校验）
    PRODUCT_PREFIX = ('00-', '01-', '02-', 'ch', 'apx-')
    for path, text in md_texts.items():
        for ref in re.findall(r'`([\w\-./]+\.md)`', text):
            if ref.startswith(('http', '/')):
                continue
            base = os.path.basename(ref)
            if base.startswith(PRODUCT_PREFIX):
                continue
            candidates = [os.path.join(root, ref),
                          os.path.join(os.path.dirname(path), ref)]
            if not any(os.path.exists(c) for c in candidates):
                rep.warn('S02', rel(path, root), f'引用的文件不存在：{ref}')

    # S03 别名映射：文档里的小节别称 → 实际标题
    ALIAS = {
        '题目元数据表': '题目元数据表',
        '硬规则': '五条硬规则',
        '章节编号约定': '章节编号约定',
        '答案规约': '答案规约',
        '来源引用格式': '来源引用格式',
        '冲突与订正标注位置': '冲突与订正标注位置',
        '题目标注与去重': '题目标注与去重',
    }
    for path, text in md_texts.items():
        for sec in re.findall(r'(?:编写规范|成书流程)\.md`?\s*的?\s*「([\u4e00-\u9fa5]{2,12})」', text):
            target = ALIAS.get(sec, sec)
            found = any(re.search(rf'^#+\s.*{re.escape(target)}', t, re.M)
                        for t in md_texts.values())
            if not found:
                rep.warn('S03', rel(path, root), f'引用的章节「{sec}」未找到定义')

    # S04 三份模板的占位符风格一致
    skeleton = read(os.path.join(root, 'assets/全书骨架.md'))
    chapter = read(os.path.join(root, 'assets/章模板.md'))
    spec = read(os.path.join(root, 'assets/编写规范.md'))
    if '`N = {填定值}`' not in skeleton and 'N = {填定值}' not in skeleton:
        rep.error('S04', 'assets/全书骨架.md', '缺少 N 的定值占位')
    for token in ('ch{N+1}', 'ch{N+2}', 'apx-a', 'apx-b'):
        if token not in skeleton:
            rep.error('S04', 'assets/全书骨架.md', f'缺少 {token} 章号')
        if token not in spec:
            rep.error('S04', 'assets/编写规范.md', f'章节编号约定缺 {token}')

    # S05 SKILL.md 提到的模式与 assets/ 一致
    skill = read(os.path.join(root, 'SKILL.md'))
    for need in ('成书模式', '建骨架模式'):
        if need not in skill:
            rep.error('S05', 'SKILL.md', f'缺少模式：{need}')
    for fname in ('00-编写规范.md', '01-全书骨架.md', '02-章模板.md'):
        if fname not in skill:
            rep.error('S05', 'SKILL.md', f'落盘结构缺少 {fname}')

    # S06 答案规约与 X.5 的指引互相指向
    if '答案规约' not in chapter:
        rep.error('S06', 'assets/章模板.md', 'X.5 未指向答案规约')
    if '### 答案规约' not in spec:
        rep.error('S06', 'assets/编写规范.md', '缺少「答案规约」小节')

    # S07 缺口类型单一权威：只在编写规范定义，成书流程必须指向它
    flow = read(os.path.join(root, 'references/成书流程.md'))
    for gt in GAP_TYPES:
        if gt not in spec:
            rep.error('S07', 'assets/编写规范.md', f'缺口类型缺 {gt}')
    if '编写规范.md' not in flow or '缺口清单' not in flow:
        rep.error('S07', 'references/成书流程.md',
                  '未指向编写规范的「缺口清单」，可能导致两处定义漂移')
    # 只在「枚举式」出现时才判为重复定义（A / B / C 或 A、B、C）
    enum_like = re.findall(
        r'(?:' + '|'.join(map(re.escape, GAP_TYPES)) + r')(?:\s*[/、]\s*(?:'
        + '|'.join(map(re.escape, GAP_TYPES)) + r')){2,}', flow)
    if enum_like:
        rep.warn('S07', 'references/成书流程.md',
                 f'枚举了缺口类型 {enum_like[0][:40]}…，应只引用规范')

    # S08 SKILL.md 必须把检查器写成收尾必做项
    if 'check_book.py' not in skill:
        rep.error('S08', 'SKILL.md', '未将 check_book.py 写为必做检查步骤')

    # S09 学科无关性：规范必须保留映射表，术语不得只留数理形态
    if '学科无关' not in spec:
        rep.error('S09', 'assets/编写规范.md', '缺少「学科无关原则」声明')
    for token in ('论证链', '核心命题', '论证策略'):
        if token not in spec:
            rep.error('S09', 'assets/编写规范.md', f'学科无关映射表缺「{token}」')
    # S09 骨架与章模板必须承认多学科形态，不能只写「公式」「定理」
    if '{公式占位}' in skeleton and '{关键史实/判据}' not in skeleton:
        rep.error('S09', 'assets/全书骨架.md',
                  '速查总表只保留了数理形态（公式占位），缺人文/工程变体')
    # 只检查 X.2 的固定顺序行，说明性引用不算
    for line in chapter.split('\n'):
        if '作用先说' in line and '定义/定理' in line and '核心概念' not in line:
            rep.error('S09', 'assets/章模板.md',
                      'X.2 仍只写数理形态「定义/定理」，应为「核心概念/命题」')


# ---------------------------------------------------------------- 入口

def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    if argv[1] == '--self':
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        rep = Report()
        check_self(root, rep)
        rep.dump('skill 包自校验')
    else:
        root = os.path.abspath(argv[1])
        if not os.path.isdir(root):
            print(f'目录不存在：{root}')
            return 2
        rep = Report()
        check_book(root, rep)
        rep.dump(f'教材校验：{root}')
    return 1 if rep.errors else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
