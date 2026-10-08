from argparse import ArgumentParser, RawTextHelpFormatter
from enum import Enum
from typing import Dict, List, Literal, TypeAlias, Union
from confz import BaseConfig, CLArgSource, EnvSource, FileSource
from pydantic import ByteSize, Field, NonNegativeInt, PositiveInt
from pydantic_extra_types.pendulum_dt import Duration
from pydantic_core import Url
from pathlib import Path

from javsp.lib import resource_path

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
    fc2fan = 'fc2fan'
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
    arzon = 'arzon'
    arzon_iv = 'arzon_iv'

class Network(BaseConfig):
    proxy_server: Url | None
    # ge=1 而非 0: retry=0 会让 core.parallel_crawler 的 for cnt in range(retry)
    # 一次都不执行 -> success 标记永不置位 -> 全部站点结果被丢弃且无任何错误提示(静默全失败)
    retry: int = Field(3, ge=1, le=10)
    timeout: Duration
    proxy_free: Dict[CrawlerID, Url]

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
    fc2fan_local_path: Path | None
    sleep_after_scraping: Duration
    use_javdb_cover: UseJavDBCover
    normalize_actress_name: bool
    max_concurrency: int = Field(5, ge=1, le=32)  # 刮削并发上限(各爬虫线程池大小), 防止瞬时全开打爆出口/代理
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

class BingTranslateEngine(BaseConfig):
    name: Literal['bing']
    api_key: str

class ClaudeTranslateEngine(BaseConfig):
    name: Literal['claude']
    api_key: str

class OpenAITranslateEngine(BaseConfig):
    name: Literal['openai']
    url: Url
    api_key: str
    model: str

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

class Other(BaseConfig):
    interactive: bool
    check_update: bool
    auto_update: bool

def get_config_source():
    parser = ArgumentParser(prog='JavSP', description='汇总多站点数据的AV元数据刮削器', formatter_class=RawTextHelpFormatter)
    parser.add_argument('-c', '--config', help='使用指定的配置文件')
    args, _ = parser.parse_known_args()
    sources = []
    if args.config is None:
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
    CONFIG_SOURCES=get_config_source()
