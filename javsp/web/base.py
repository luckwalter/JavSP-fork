"""网络请求的统一接口"""
import os
import sys
import time
import shutil
import logging
import requests
import contextlib
import cloudscraper
from functools import partial
import lxml.html
from tqdm import tqdm
from lxml import etree
from lxml.html.clean import Cleaner
from requests.models import Response


from javsp.config import Cfg
from javsp.web.exceptions import *


__all__ = ['Request', 'tls_verify', 'get_html', 'post_html', 'request_get', 'resp2html', 'is_connectable', 'download', 'get_resp_text', 'read_proxy']


headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'}

logger = logging.getLogger(__name__)
# 删除js脚本相关的tag，避免网页检测到没有js运行环境时强行跳转，影响调试
cleaner = Cleaner(kill_tags=['script', 'noscript'])

def read_proxy():
    if Cfg().network.proxy_server is None:
        return {}
    else:
        proxy = str(Cfg().network.proxy_server)
        return {'http': proxy, 'https': proxy}


def tls_verify():
    """TLS 证书校验的目标, 传给 requests 的 verify= 参数

    绝大多数情况返回 True(= CA 证书包路径, requests 默认行为, 校验开启)。
    这里只处理一种正当需求: **HTTP(S) 代理做 TLS 解密(MITM)** 时, 代理会用自己的
    根证书重签目标站证书。此时目标站证书对该代理的 CA 而言是「未知签发者」,
    默认校验必然报 SSLCertVerificationError, 导致**所有**走代理的站点全挂。

    解决方式是把代理的根证书挂进容器并通过环境变量告知路径(而非关闭校验):
        - `JAVSP_CA_BUNDLE=/etc/ssl/certs/myproxy.crt`  —— 指定自定义 CA 包;
        - `JAVSP_TLS_VERIFY=0`                          —— 显式关闭(不推荐, 仅内网自控)。

    未设置时行为与原来完全一致(校验开启), 不改变默认安全性。
    """
    bundle = os.getenv('JAVSP_CA_BUNDLE', '').strip()
    if bundle:
        if os.path.isfile(bundle):
            return bundle
        logger.warning(
            f'JAVSP_CA_BUNDLE 指向的 CA 文件不存在: {bundle}; 退回默认证书校验')
        return True
    if os.getenv('JAVSP_TLS_VERIFY', '').strip() in ('0', 'false', 'False'):
        logger.warning('JAVSP_TLS_VERIFY=0: 已按环境变量关闭 TLS 证书校验(不安全, 仅限受控内网)')
        return False
    return True

# 与网络请求相关的功能汇总到一个模块中以方便处理，但是不同站点的抓取器又有自己的需求（针对不同网站
# 需要使用不同的UA、语言等）。每次都传递参数很麻烦，而且会面临函数参数越加越多的问题。因此添加这个
# 处理网络请求的类，它带有默认的属性，但是也可以在各个抓取器模块里进行进行定制
class Request():
    """作为网络请求出口并支持各个模块定制功能"""
    def __init__(self, use_scraper=False) -> None:
        # 必须使用copy()，否则各个模块对headers的修改都将会指向本模块中定义的headers变量，导致只有最后一个对headers的修改生效
        self.headers = headers.copy()
        self.cookies = {}

        self.proxies = read_proxy()
        self.timeout = Cfg().network.timeout.total_seconds()
        # 统一在此处注入 verify, 而不是让 40+ 个调用点各自记得传 —— 漏一个就等于
        # 该站点在 MITM 代理下静默失效。用 partial 绑定比逐处改更不易漏。
        _verify = tls_verify()
        _get = partial(requests.get, verify=_verify)
        _post = partial(requests.post, verify=_verify)
        _head = partial(requests.head, verify=_verify)
        if not use_scraper:
            self.scraper = None
            self.__get = _get
            self.__post = _post
            self.__head = _head
        else:
            self.scraper = cloudscraper.create_scraper()
            # cloudscraper 的方法同样要绑定 verify; 回退路径用普通 requests
            self.__get = self._scraper_monitor(partial(self.scraper.get, verify=_verify), _get)
            self.__post = self._scraper_monitor(partial(self.scraper.post, verify=_verify), _post)
            self.__head = self._scraper_monitor(partial(self.scraper.head, verify=_verify), _head)

    def _scraper_monitor(self, func, fallback):
        """监控cloudscraper的工作状态，遇到不支持的Challenge时尝试退回常规的requests请求

        fallback 必须由调用方显式指定: 原实现只区分 get 与「其他」, 导致 HEAD 请求
        失败时被回退成 POST(调用点 javdb 用它校验封面是否存在)—— 对只接受 HEAD 的
        服务器产生非预期副作用, 对接受 POST 的服务器则等于以登录凭据发起写操作。
        """
        def wrapper(*args, **kw):
            try:
                return func(*args, **kw)
            except Exception as e:
                logger.debug(f"无法通过CloudFlare检测: '{e}', 尝试退回常规的requests请求")
                return fallback(*args, **kw)
        return wrapper

    def get(self, url, delay_raise=False):
        r = self.__get(url,
                      headers=self.headers,
                      proxies=self.proxies,
                      cookies=self.cookies,
                      timeout=self.timeout)
        if not delay_raise:
            r.raise_for_status()
        return r

    def post(self, url, data, delay_raise=False):
        r = self.__post(url,
                      data=data,
                      headers=self.headers,
                      proxies=self.proxies,
                      cookies=self.cookies,
                      timeout=self.timeout)
        if not delay_raise:
            r.raise_for_status()
        return r

    def head(self, url, delay_raise=True):
        r = self.__head(url,
                      headers=self.headers,
                      proxies=self.proxies,
                      cookies=self.cookies,
                      timeout=self.timeout)
        if not delay_raise:
            r.raise_for_status()
        return r

    def get_html(self, url):
        r = self.get(url)
        html = resp2html(r)
        return html


class DownloadProgressBar(tqdm):
    def update_to(self, b=1, bsize=1, tsize=None):
        if tsize is not None:
            self.total = tsize
        self.update(b * bsize - self.n)


def request_get(url, cookies={}, timeout=None, delay_raise=False):
    """获取指定url的原始请求"""
    if timeout is None:
        timeout = Cfg().network.timeout.seconds
    
    r = requests.get(url, headers=headers, proxies=read_proxy(), cookies=cookies, timeout=timeout, verify=tls_verify())
    if not delay_raise:
        if r.status_code == 403 and b'>Just a moment...<' in r.content:
            raise SiteBlocked(f"403 Forbidden: 无法通过CloudFlare检测: {url}")
        else:
            r.raise_for_status()
    return r


def request_post(url, data, cookies={}, timeout=None, delay_raise=False):
    """向指定url发送post请求"""
    if timeout is None:
        timeout = Cfg().network.timeout.seconds
    r = requests.post(url, data=data, headers=headers, proxies=read_proxy(), cookies=cookies, timeout=timeout, verify=tls_verify())
    if not delay_raise:
        r.raise_for_status()
    return r


def get_resp_text(resp: Response, encoding=None):
    """提取Response的文本"""
    if encoding:
        resp.encoding = encoding
    else:
        resp.encoding = resp.apparent_encoding
    return resp.text


def get_html(url, encoding='utf-8'):
    """使用get方法访问指定网页并返回经lxml解析后的document"""
    resp = request_get(url)
    text = get_resp_text(resp, encoding=encoding)
    html = lxml.html.fromstring(text)
    html.make_links_absolute(url, resolve_base_href=True)
    # 清理功能仅应在需要的时候用来调试网页（如prestige），否则可能反过来影响调试（如JavBus）
    # html = cleaner.clean_html(html)
    if hasattr(sys, 'javsp_debug_mode'):
        lxml.html.open_in_browser(html, encoding=encoding)  # for develop and debug
    return html


def resp2html(resp, encoding='utf-8') -> lxml.html.HtmlComment:
    """将request返回的response转换为经lxml解析后的document"""
    text = get_resp_text(resp, encoding=encoding)
    html = lxml.html.fromstring(text)
    html.make_links_absolute(resp.url, resolve_base_href=True)
    # html = cleaner.clean_html(html)
    if hasattr(sys, 'javsp_debug_mode'):
        lxml.html.open_in_browser(html, encoding=encoding)  # for develop and debug
    return html


def post_html(url, data, encoding='utf-8', cookies={}):
    """使用post方法访问指定网页并返回经lxml解析后的document"""
    resp = request_post(url, data, cookies=cookies)
    text = get_resp_text(resp, encoding=encoding)
    html = lxml.html.fromstring(text)
    # jav321提供ed2k形式的资源链接，其中的非ASCII字符可能导致转换失败，因此要先进行处理
    ed2k_tags = html.xpath("//a[starts-with(@href,'ed2k://')]")
    for tag in ed2k_tags:
        tag.attrib['ed2k'], tag.attrib['href'] = tag.attrib['href'], ''
    html.make_links_absolute(url, resolve_base_href=True)
    for tag in ed2k_tags:
        tag.attrib['href'] = tag.attrib['ed2k']
        tag.attrib.pop('ed2k')
    # html = cleaner.clean_html(html)
    # lxml.html.open_in_browser(html, encoding=encoding)  # for develop and debug
    return html


def dump_xpath_node(node, filename=None):
    """将xpath节点dump到文件"""
    if not filename:
        filename = node.tag + '.html'
    with open(filename, 'wt', encoding='utf-8') as f:
        content = etree.tostring(node, pretty_print=True).decode('utf-8')
        f.write(content)


def is_connectable(url, timeout=3):
    """测试与指定url的连接"""
    try:
        r = requests.get(url, headers=headers, timeout=timeout, verify=tls_verify())
        return True
    except requests.exceptions.RequestException as e:
        logger.debug(f"Not connectable: {url}\n" + repr(e))
        return False


def urlretrieve(url, filename=None, reporthook=None, headers=None, max_bytes=None):
    if "arzon" in url:
        headers["Referer"] = "https://www.arzon.jp/"
    """使用requests实现urlretrieve"""
    # https://blog.csdn.net/qq_38282706/article/details/80253447
    with contextlib.closing(requests.get(url, headers=headers,
                                         proxies=read_proxy(), stream=True,
                                         verify=tls_verify())) as r:
        header = r.headers
        # 下载体积上限: 封面/剧照本应是几 MB 的图片, 超过上限说明要么是异常响应,
        # 要么是被重定向到了别处(可能是内网探测响应)。不设限时可能把磁盘写满。
        limit = _MAX_DOWNLOAD_BYTES if max_bytes is None else max_bytes
        size = -1
        if "content-length" in header:
            try:
                size = int(header["Content-Length"])
            except ValueError:
                size = -1
            if size > limit:
                raise ValueError(f'响应体过大({size} > {limit} 字节), 已中止下载: {url[:80]}')
        written = 0
        with open(filename, 'wb+') as fp:
            bs = 1024
            blocknum = 0
            if reporthook:                              # 写入前运行一次回调函数
                reporthook(blocknum, bs, size)
            for chunk in r.iter_content(chunk_size=1024):
                if chunk:
                    written += len(chunk)
                    if written > limit:
                        raise ValueError(f'下载超过上限({limit} 字节), 已中止: {url[:80]}')
                    fp.write(chunk)
                    fp.flush()
                    blocknum += 1
                    if reporthook:
                        reporthook(blocknum, bs, size)  # 每写入一次运行一次回调函数


# 单文件下载上限: 封面 8-10MiB 量级, 留足余量到 64MiB(剧照/海报都比封面小)
_MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024


def download(url, output_path, desc=None):
    """下载指定url的资源"""
    # 只允许 http/https。原实现是「非 http 开头就当本地路径复制」, 而 url 来自
    # 爬虫解析到的 cover/big_covers(远端站点 HTML 可控) —— 站点若返回 /etc/passwd
    # 之类就会走 shutil.copyfile 把本机文件复制出去(任意文件读取原语)。
    # fc2fan 的本地镜像需求改由显式的 file:// 前缀表达。
    if url.startswith('file://'):
        local = url[len('file://'):]
        start_time = time.time()
        shutil.copyfile(local, output_path)
        filesize = os.path.getsize(local)
        elapsed = time.time() - start_time
        return {'total': filesize, 'elapsed': elapsed, 'rate': filesize / elapsed}
    if not url.startswith(('http://', 'https://')):
        raise ValueError(f'不支持的 URL 协议(仅允许 http/https): {url[:80]}')
    if not desc:
        desc = url.split('/')[-1]
    referrer = headers.copy()
    referrer['referer'] = url[:url.find('/', 8)+1]  # 提取base_url部分
    with DownloadProgressBar(unit='B', unit_scale=True,
                             miniters=1, desc=desc, leave=False) as t:
        urlretrieve(url, filename=output_path, reporthook=t.update_to, headers=referrer)
        info = {k: t.format_dict[k] for k in ('total', 'elapsed', 'rate')}
        return info


def open_in_chrome(url, new=0, autoraise=True):
    """使用指定的Chrome Profile打开url，便于调试"""
    import subprocess
    chrome = R'C:\Program Files\Google\Chrome\Application\chrome.exe'
    subprocess.run(f'"{chrome}" --profile-directory="Profile 2" {url}', shell=True)

import webbrowser
webbrowser.open = open_in_chrome


if __name__ == "__main__":
    import pretty_errors
    pretty_errors.configure(display_link=True)
    download('https://www.javbus.com/pics/cover/6n54_b.jpg', 'cover.jpg')
