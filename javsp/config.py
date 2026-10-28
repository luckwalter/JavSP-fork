from argparse import ArgumentParser, RawTextHelpFormatter
from enum import Enum
from typing import Dict, List, Literal, TypeAlias, Union
from confz import BaseConfig, CLArgSource, EnvSource, FileSource
from pydantic import ByteSize, ConfigDict, Field, PositiveInt
from pydantic_extra_types.pendulum_dt import Duration
from pydantic_core import Url
from pathlib import Path

import os
import sys
import shutil
import logging

from javsp.lib import resource_path

logger = logging.getLogger(__name__)

class Scanner(BaseConfig):
    ignored_id_pattern: List[str]
    input_directory: Path | None = None
    filename_extensions: List[str]
    ignored_folder_name_pattern: List[str]
    minimum_size: ByteSize
    skip_nfo_dir: bool
    manual: bool

class CrawlerID(str, Enum):
    airav = 'airav'
    avsox = 'avsox'
    avwiki = 'avwiki'
    dl_getchu = 'dl_getchu'
    fanza = 'fanza'
    fc2 = 'fc2'
    fc2ppvdb = 'fc2ppvdb'
    gyutto = 'gyutto'
    jav321 = 'jav321'
    javbus = 'javbus'
    javdb = 'javdb'
    javlib = 'javlib'
    javmenu = 'javmenu'
    # v0.2.1 新增: JavBoss 研究移植的三源(在 NAS 受限出口下实测可达, 补位已废/被墙的老源)
    javdbapi = 'javdbapi'      # javdb 手机 App 私有 API(纯 JSON, 绕开 Cloudflare)
    javdatabase = 'javdatabase'  # javdatabase.com 详情页
    # 注: javmenu 未新增枚举 —— 该名字原本就指向死站 mrzyx.xyz, v0.2.1 已就地替换为 javmenu.com
    mgstage = 'mgstage'
    njav = 'njav'
    prestige = 'prestige'
    javfree = 'javfree'   # v0.2.6 新增: javfree.me 番号库(WordPress), 日本出口下可达, 补位已废/被墙老源

class Network(BaseConfig):
    # 保底: 旧配置若残留 proxy_free 等已废弃字段, 忽略而非整体校验失败(不影响其它功能)
    model_config = ConfigDict(extra='ignore')
    proxy_server: Url | None
    # ge=1 而非 0: retry=0 会让 core.parallel_crawler 的 for cnt in range(retry)
    # 一次都不执行 -> success 标记永不置位 -> 全部站点结果被丢弃且无任何错误提示(静默全失败)
    retry: int = Field(3, ge=1, le=10)
    timeout: Duration

class CrawlerSelect(BaseConfig):
    def items(self) -> List[tuple[str, list[CrawlerID]]]:
        return [
            ('normal', self.normal),
            ('fc2', self.fc2),
            ('cid', self.cid),
            ('getchu', self.getchu),
            ('gyutto', self.gyutto),
        ]

    def __getitem__(self, index) -> list[CrawlerID]:
        match index:
            case 'normal':
                return self.normal
            case 'fc2':
                return self.fc2
            case 'cid':
                return self.cid
            case 'getchu':
                return self.getchu
            case 'gyutto':
                return self.gyutto
        raise Exception("Unknown crawler type")

    normal: list[CrawlerID]
    fc2: list[CrawlerID]
    cid: list[CrawlerID]
    getchu: list[CrawlerID]
    gyutto: list[CrawlerID]

class MovieInfoField(str, Enum):
    dvdid = 'dvdid'
    cid = 'cid'
    url = 'url'
    plot = 'plot'
    cover = 'cover'
    big_cover = 'big_cover'
    genre = 'genre'
    genre_id = 'genre_id'
    genre_norm = 'genre_norm'
    score = 'score'
    title = 'title'
    ori_title = 'ori_title'
    magnet = 'magnet'
    serial = 'serial'
    actress = 'actress'
    actress_pics = 'actress_pics'
    director = 'director'
    duration = 'duration'
    producer = 'producer'
    publisher = 'publisher'
    uncensored = 'uncensored'
    publish_date = 'publish_date'
    preview_pics = 'preview_pics'
    preview_video = 'preview_video'

class UseJavDBCover(str, Enum):
    yes = "yes"
    no = "no"
    fallback = "fallback"

class Crawler(BaseConfig):
    selection: CrawlerSelect
    required_keys: list[MovieInfoField]
    hardworking: bool
    respect_site_avid: bool
    sleep_after_scraping: Duration
    use_javdb_cover: UseJavDBCover
    normalize_actress_name: bool
    max_concurrency: int = Field(5, ge=1, le=32)  # 刮削并发上限(各爬虫线程池大小), 防止瞬时全开打爆出口/代理
    # 【已废弃 2026-10-12】各渠道浏览器 cookie。javbus 的年龄验证已改为内置 dv=1
    # cookie(见 web/javbus.py), 不再需要用户手动粘贴; 前端「配置 Cookie」入口与
    # 后端 /api/channels/{source}/cookie 接口均已删除。
    # 字段保留仅为兼容: 挂载卷里的旧 config.yml 可能仍带 crawler.cookies 块,
    # 删字段会让 confz 校验失败导致服务起不来(挂载卷配置不随镜像更新)。
    # 新部署可直接从 config.yml 中移除该块。
    cookies: Dict[str, list] = {}
    # 上界 32: ThreadPoolExecutor(max_workers) 直接决定线程数, 设过大(如 10^7)会耗尽线程/内存

class MovieDefault(BaseConfig):
    title: str
    actress: str
    series: str
    director: str
    producer: str
    publisher: str

class PathSummarize(BaseConfig):
    output_folder_pattern: str
    basename_pattern: str
    length_maximum: PositiveInt
    length_by_byte: bool
    max_actress_count: PositiveInt = 10
    hard_link: bool

class TitleSummarize(BaseConfig):
    remove_trailing_actor_name: bool

class NFOSummarize(BaseConfig):
    # 是否生成 nfo 文件（由 Jellyfin/Emby 自行刮削元数据时可以关闭）
    enabled: bool = True
    basename_pattern: str
    title_pattern: str
    custom_genres_fields: list[str]
    custom_tags_fields: list[str]

class ExtraFanartSummarize(BaseConfig):
    enabled: bool
    scrap_interval: Duration

class SlimefaceEngine(BaseConfig):
    name: Literal['slimeface']

class CoverCrop(BaseConfig):
  engine: SlimefaceEngine | None
  on_id_pattern: list[str]

class CoverSummarize(BaseConfig):
    # 是否生成封面图(poster)。poster 由下载的原图裁剪而来，关掉它可以省下裁剪开销
    enabled: bool = True
    basename_pattern: str
    highres: bool
    add_label: bool
    crop: CoverCrop

class FanartSummarize(BaseConfig):
    # 是否保留横版封面图(fanart)。封面下载与 poster 同源，关掉后原图会在生成 poster 后被删除
    enabled: bool = True
    basename_pattern: str

class Summarizer(BaseConfig):
    default: MovieDefault
    censor_options_representation: list[str]
    title: TitleSummarize
    move_files: bool = True
    path: PathSummarize
    nfo: NFOSummarize
    cover: CoverSummarize
    fanart: FanartSummarize
    extra_fanarts: ExtraFanartSummarize

class BaiduTranslateEngine(BaseConfig):
    name: Literal['baidu']
    app_id: str
    api_key: str
    # 每秒请求数上限(QPS)。百度标准版 QPS=1, 高级版/企业版可达 10; 代码按此值
    # 在请求前节流, 超限会被百度拒绝(错误码 54003)。默认 1.0 与历史行为一致
    # (旧实现是硬编码 sleep 1 秒, 高级版用户被白白限速 10 倍)。
    qps: float = Field(1.0, ge=0.1, le=20)

class BingTranslateEngine(BaseConfig):
    name: Literal['bing']
    api_key: str
    # Azure 资源所在区域。多服务/全球资源用 global; 单服务资源(如 eastasia)
    # 必须填实际区域, 否则返回 401001 且用户无从判断原因。默认 global 兼容旧配置。
    region: str = 'global'

class ClaudeTranslateEngine(BaseConfig):
    name: Literal['claude']
    api_key: str
    # ⚠️ 历史默认 claude-3-haiku-20240307 已被 Anthropic **硬退役**: 自
    # 2026-04-20 起对该模型 ID 的所有请求直接报错(官方推荐迁移到 Haiku 4.5),
    # 故默认值改为在役型号。翻译是高频短文本任务, haiku 系列性价比最高;
    # 需要更强的语种遵循时可手写 claude-sonnet-4-6 等。
    model: str = 'claude-haiku-4-5-20251001'
    # 是否走本项目通用代理(network.proxy_server)。国内访问 Anthropic 需要代理,
    # 而 translate.py 的设计是翻译服务默认不走代理, 故显式开关。
    # 默认 False 兼容旧配置(缺省即直连)。界面上的「使用统一代理」会覆盖它。
    use_proxy: bool = False

class OpenAITranslateEngine(BaseConfig):
    name: Literal['openai']
    url: Url
    # 可留空: 本地 Ollama / LM Studio 不需要鉴权, 留空时不发送 Authorization 头
    api_key: str = ''
    model: str
    # 同 ClaudeTranslateEngine.use_proxy。本地部署(Ollama 等)填局域网地址时保持
    # False; 云端源(Groq/OpenRouter/Gemini 等)国内直连不通, 打开即走项目代理。
    use_proxy: bool = False

class GoogleTranslateEngine(BaseConfig):
    name: Literal['google']

TranslateEngine: TypeAlias = Union[
        BaiduTranslateEngine,
        BingTranslateEngine,
        ClaudeTranslateEngine,
        OpenAITranslateEngine,
        GoogleTranslateEngine,
        None]

class TranslateField(BaseConfig):
    title: bool
    plot: bool

class Translator(BaseConfig):
    engine: TranslateEngine = Field(..., discriminator='name')
    fields: TranslateField
    # 翻译目标语种: zh_CN(简体中文)/zh_TW(繁体中文)/en/ja/fr/de/es
    # 旧配置无此字段时默认简体中文, 保持兼容
    language: str = 'zh_CN'
    # 是否走「网络与容错」里配置的出口代理(network.proxy_server)——**对所有翻译
    # 引擎通用**。此前三个行为互不一致且各自藏在不同地方: Google 无条件走代理、
    # 百度/Bing 完全不走、claude/openai 各带一个 engine.use_proxy 开关。
    #
    # None = 未设置, 回落旧配置里 claude/openai 自带 engine.use_proxy(默认 False);
    #   baidu/bing/google 无该字段 -> False。是否走代理**完全由用户手动选择**,
    #   不保留任何引擎的特殊默认(Google 也不再有"无条件走代理"的特例)。
    # 显式 true / false 则覆盖所有引擎(界面开关只写这两个值)。
    use_proxy: bool | None = None

class Other(BaseConfig):
    interactive: bool
    check_update: bool
    auto_update: bool

class Cache(BaseConfig):
    """本地缓存(缩略图 + 翻译结果)

    thumbs_dir: 缩略图目录。留空 = 自动判定(容器 /data/.javsp_cache/thumbs,
        桌面版 exe 同级 cache/thumbs)。点开头的隐藏目录可避免被媒体服务器索引。
    auto_clean / retention_days: 缩略图的自动清理开关与保留天数(默认 7 天)。

    translation_enabled: 是否缓存翻译结果。刮削同一部影片(或重跑失败任务)时
        直接复用上次译文, 省 API 调用与配额; 换引擎/模型/目标语种/文本任一
        变化都会自动失效, 不会串味。
    translation_dir: 翻译缓存目录, 留空 = 与缩略图同级目录下的 translations。
    translation_retention_days: 翻译缓存保留天数(默认 30 天)。
    """
    thumbs_dir: str = ''
    auto_clean: bool = True
    retention_days: int = Field(7, ge=1, le=365)
    translation_enabled: bool = True
    translation_dir: str = ''
    translation_retention_days: int = Field(30, ge=1, le=365)

def get_config_source():
    parser = ArgumentParser(prog='JavSP', description='汇总多站点数据的AV元数据刮削器', formatter_class=RawTextHelpFormatter)
    parser.add_argument('-c', '--config', help='使用指定的配置文件')
    args, _ = parser.parse_known_args()
    sources = []
    if args.config is None:
        if getattr(sys, 'frozen', False):
            # 打包形态: 配置落到 exe 同级目录(便携), 整包升级也不会被 _internal 覆盖
            exe_dir = os.path.dirname(os.path.abspath(sys.executable))
            user_cfg = os.path.join(exe_dir, 'config.yml')
            default_cfg = os.path.join(exe_dir, '_internal', 'config.yml')
            if not os.path.isfile(user_cfg):
                if os.path.isfile(default_cfg):
                    # 首次运行: 从打包内置的默认模板初始化到 exe 同级
                    try:
                        shutil.copyfile(default_cfg, user_cfg)
                        logger.info('已从内置模板初始化配置文件: %s', user_cfg)
                    except OSError as e:
                        logger.warning('初始化默认配置失败(%s), 将直接使用内置模板', e)
                        user_cfg = default_cfg
                else:
                    logger.warning('未找到内置默认配置模板, 回退路径: %s', user_cfg)
            args.config = user_cfg
        else:
            args.config = resource_path('config.yml')
    sources.append(FileSource(file=args.config))
    # ⚠️ EnvSource 的嵌套层级分隔符是 confz 的默认值 '.', 本项目**刻意未改**。
    # 因此嵌套环境变量必须写点号: JAVSP_NETWORK.PROXY_SERVER / JAVSP_TRANSLATOR.ENGINE.API_KEY
    # 写成双下划线(JAVSP_NETWORK__PROXY_SERVER)会被**静默忽略** —— 不抛异常、不生效, 读出来是 None,
    # 极易误判成「配了却没效果」(实测踩过: NAS 上因此以为代理已配, 实则全程直连导致三个源全挂)。
    # 仓库内 verify_env_nested_separator.py 对此有断言守护; README/compose 亦已注明。
    sources.append(EnvSource(prefix='JAVSP_', allow_all=True))
    sources.append(CLArgSource(prefix='o'))
    return sources

class Cfg(BaseConfig):
    scanner: Scanner
    network: Network
    crawler: Crawler
    summarizer: Summarizer
    translator: Translator
    other: Other
    cache: Cache = Cache()
    CONFIG_SOURCES=get_config_source()
