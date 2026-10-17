# -*- mode: python ; coding: utf-8 -*-
"""
JavSP 部署/测试**过程产物清理工具**(入库, 每次部署/测试收尾用)

背景: 本机版就地升级会留下 `.bakNNN` 备份目录与 `.tmp_` 中转目录, 与主目录
`C:\\JavSP` 并列, 极易被误当主目录双击(实战踩坑: 主人误开 `.bak032` 旧版,
以为没更新)。本工具在**验证新版本可用后**统一清理这些过程产物。

用法:
    python tools/cleanup.py           # 预览(dry-run), 列出将删除项, 不动任何文件
    python tools/cleanup.py --apply   # 实际删除(先跑一次预览确认清单)

安全设计:
  - 默认 dry-run, 必须显式 --apply 才删(先看清单 -> 确认 -> 再删);
  - 只删白名单模式(严格), 绝不触碰源码/配置/主部署目录/用户数据;
  - 删前逐项 guard, 主目录 `C:\\JavSP` 本身受保护跳过。
"""
import os
import sys
import glob
import shutil

# 本机版部署主目录(受保护, 永不删除)。桌面版可能同时部署在多处, 逐个列出。
DEPLOY_MAINS = [
    r'C:\JavSP',
    '\\\\HOMENAS\\Downloads\\JavSP',
]

# 备份/中转目录的前缀(在部署目录的父目录下匹配)
BACKUP_PREFIXES = ['JavSP.bak', 'JavSP.tmp_']

# 工作区(仓库上级)的一次性过程产物
WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKSPACE_PATTERNS = [
    'javsp_deploy_*.tar.gz',    # 部署临时打包
    'javsp_build_*.tar.gz',     # 镜像构建临时打包
    'javsp_deploy_*.tar',
    'javsp_build_*.tar',
    '_*_tmp.py',                # 一次性临时脚本
    '_*_tmp.txt',
    '_verify_out.txt',
]


def _size_of(path):
    """目录返回 None(不精确计算, 避免大目录 walk 慢), 文件返回字节数。"""
    if os.path.isdir(path):
        return None
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def collect():
    """收集待清理项: (path, kind)。"""
    items = []
    # 1. 本机版备份/中转: 列出部署目录的**父目录**按前缀过滤
    # (不用 glob, Windows 绝对路径+通配不可靠; 曾漏匹配过)。
    # 部署位置可能不止一处(本地 C:\JavSP 与网盘 \\HOMENAS\Downloads\JavSP),
    # 所以父目录也要从已知部署位置推导, 而不是写死 'C:\'。
    deploy_parents = set()
    for main in DEPLOY_MAINS:
        parent = os.path.dirname(main.rstrip('\\/')) or main
        deploy_parents.add(parent)
    # 兼容历史命名(裸 'JavSP.bakNNN' 位于 C 盘根)
    deploy_parents.add('C:\\')

    for root in sorted(deploy_parents):
        if not root:
            continue
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for n in names:
            for prefix in BACKUP_PREFIXES:
                if n.startswith(prefix):
                    items.append((os.path.join(root, n), '本机版备份/中转'))
                    break

    # 2. 工作区过程产物
    for pat in WORKSPACE_PATTERNS:
        for p in sorted(glob.glob(os.path.join(WORKSPACE, pat))):
            items.append((p, '工作区过程产物'))
    return items


def guard(path):
    """删除前保护: 所有部署主目录本身永不删(与具体部署位置无关)。"""
    target = os.path.normcase(os.path.abspath(path))
    for main in DEPLOY_MAINS:
        if target == os.path.normcase(os.path.abspath(main)):
            return False
    return True


def main():
    apply = '--apply' in sys.argv
    items = collect()
    if not items:
        print('未发现过程产物/备份, 无需清理。')
        return

    print('=== 过程产物/备份清理清单 ===')
    total_bytes = 0
    n_dir = 0
    for p, kind in items:
        if not guard(p):
            print(f'  [受保护-跳过] {p}')
            continue
        size = _size_of(p)
        if size is None:
            n_dir += 1
            disp = '(目录)'
        else:
            total_bytes += size
            disp = f'{size/1024/1024:.2f}MB'
        print(f'  [{kind}] {p}  {disp}')

    print(f'\n合计 {len(items)} 项 (目录 {n_dir} 个, 文件 {total_bytes/1024/1024:.2f}MB)')
    if not apply:
        print('\n(预览模式, 未删除任何文件)')
        print('确认清单无误后执行: python tools/cleanup.py --apply')
        return

    print('\n=== 执行删除 ===')
    ok = fail = 0
    for p, kind in items:
        if not guard(p):
            continue
        try:
            if os.path.isdir(p):
                shutil.rmtree(p)
            else:
                os.remove(p)
            print(f'  已删除: {p}')
            ok += 1
        except Exception as e:
            print(f'  删除失败: {p} -> {e}')
            fail += 1
    print(f'\n完成: 成功 {ok}, 失败 {fail}')


if __name__ == '__main__':
    main()