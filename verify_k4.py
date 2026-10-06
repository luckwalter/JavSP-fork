"""验证 parallel_crawler 返回的 all_info 键是否为 CrawlerID.value(如 'airav'/'javdb')
而非被 k[4:] 错误切片成 'v'/'db'。info_summary 依赖 'javdb' 这种键做 genre/封面特判。
"""
import sys
sys.path.insert(0, '.')

import javsp.core as core
from javsp.datatype import Movie
from javsp.config import Cfg


def main():
    core.import_crawlers()

    # mock 每个已导入爬虫的 parse_data: 不联网, 直接填字段并成功,
    # 这样 parallel_crawler 的多线程 wrapper 会把 info.success 置为 True
    def make_stub(name):
        def stub(info):
            info.title = f'title-{name}'
            info.genre = ['genre-' + name]
            info.cover = f'http://cover/{name}.jpg'
        return stub

    mods = {'javsp.web.' + m.value for m in Cfg().crawler.selection.normal}
    mocked = []
    for mod in mods:
        if mod in sys.modules:
            sys.modules[mod].parse_data = make_stub(mod.split('.')[-1])
            mocked.append(mod)
    print(f'[debug] 已 mock 的爬虫模块({len(mocked)}): {sorted(mocked)}')

    movie = Movie('ABC-123')
    all_info = core.parallel_crawler(movie)
    keys = sorted(all_info.keys())
    print('parallel_crawler 返回 key =', keys)
    print("'javdb' in keys =", 'javdb' in keys)

    ok = 'javdb' in keys and all(k == k.lstrip('web.') and not k.startswith(('v', 'db', 'bus', 'x', 'age')) for k in keys)
    if ok:
        print('PASS: key 保持 CrawlerID.value 形式, info_summary 的 javdb 特判可正确命中')
    else:
        print('FAIL: key 被错误切片, info_summary 的 javdb 特判会失效')
    return ok


if __name__ == '__main__':
    import sys as _s
    _s.exit(0 if main() else 1)
