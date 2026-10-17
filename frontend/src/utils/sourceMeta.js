/**
 * 刮削源元数据 —— 选源能力集 / 分组 / 预设 / 站点说明的单一数据源。
 * 供 ChannelsView(刮削源页)与 SettingsView(高级参数)共用, 避免两处各写一份后漂移。
 *
 * 与后端契约的对应关系:
 *   - 源清单全集 = javsp/config.py 的 CrawlerID 枚举(19 个)
 *   - 选源配置 = config.py 的 CrawlerSelect 五维清单(normal/fc2/cid/getchu/gyutto),
 *     刮削实际只消费 normal/fc2/cid 三类(core.py 按 movie.data_src 取清单),
 *     getchu/gyutto 两维仅 import_crawlers 启动时遍历; 页面**不为不在能力集的源**渲染
 *     开关与类别 chip —— 它们归入常规分组作只读展示, 不再单列"不参与刮削"组。
 *   - 页面没有"不参与刮削"这个分类: 能力集为空(avwiki/dl_getchu/gyutto 中后两者)的源
 *     既不计入启用/未启用统计, 也不提供开关, 仅靠副标题与探活状态说明其本质。
 */

/** 刮削消费的三类选源维度(与 core.py 的 movie.data_src 对应) */
export const KINDS = ['normal', 'fc2', 'cid']
export const KIND_LABEL = { normal: '普通', fc2: 'FC2', cid: 'CID' }

/**
 * 每个源实际支持的番号类型(据此收敛 chip: 只渲染源真实支持的类别)。
 * 来源: SettingsView 历史推荐清单, 与 config.yml 默认 selection 对齐;
 * dl_getchu/gyutto 不在任何维度(它们是 getchu/gyutto 维度专属), 故不会出现在
 * 任何类别 chip 里, 归入常规分组作只读展示。
 */
export const SOURCE_KINDS = {
  normal: ['airav', 'avsox', 'avwiki', 'jav321', 'javbus', 'javdb', 'javdbapi',
           'javdatabase', 'javlib', 'javmenu', 'mgstage', 'prestige', 'njav', 'javfree'],
  fc2: ['fc2', 'fc2ppvdb', 'avsox', 'javdb', 'javmenu'],
  cid: ['fanza'],
}

/** 有能力集的源全集(normal/fc2/cid 任一维度支持的源); 之外的源(如 dl_getchu/gyutto)
 *  不渲染开关与类别 chip, 归入常规分组作只读展示, 不参与"启用/未启用"统计 */
export const CAPABLE_SOURCES = [...new Set(KINDS.flatMap((k) => SOURCE_KINDS[k]))]

/**
 * 页面分组(按源的主要身份, 一个源只出现在一个组)。
 * 顺序即展示顺序; 兜底组 'other' 接住 CrawlerID 未来新增但未在此声明的源。
 */
export const SOURCE_GROUPS = [
  {
    key: 'lib', label: '普通番号库', hint: '通用番号库, 按顺序互补兜底',
    sources: ['javdb', 'javbus', 'javlib', 'jav321', 'airav', 'javdbapi',
              'javdatabase', 'javmenu', 'njav', 'javfree', 'avsox', 'avwiki'],
  },
  {
    key: 'fc2', label: 'FC2 专题', hint: 'FC2-xxxx 番号专属源',
    sources: ['fc2', 'fc2ppvdb'],
  },
  {
    key: 'studio', label: '厂商官方', hint: '制作商 / 官方渠道直营站点, 数据最权威但覆盖窄',
    sources: ['mgstage', 'prestige', 'dl_getchu', 'gyutto'],
  },
  {
    key: 'cid', label: 'CID 专题', hint: 'CID 番号(需 cid 字段才能检索)',
    sources: ['fanza'],
  },
]

/** 站点说明(行的副标题, 一句话说清这个源是什么) */
export const SITE_LABELS = {
  javdb: 'javdb.com 番号库',
  javbus: 'javbus.com 番号库',
  javlib: 'javlib.com 番号库',
  jav321: 'jav321.com(源站整体宕机, 无法复活)',
  airav: 'airav.io(按 hid 寻址)',
  avsox: '前端渲染·不可用',
  avwiki: '女优资料站（av-wiki.net，非番号库）',
  mgstage: '官方直营(单体厂商)',
  prestige: '官方直营(单体厂商)',
  javdbapi: 'javdb App API(绕开 Cloudflare)',
  javdatabase: 'javdatabase.com 详情页',
  javmenu: 'javmenu.com',
  njav: '无码番号库',
  javfree: 'javfree.me(WordPress)',
  fc2: 'FC2 官方',
  fc2ppvdb: 'FC2 数据库',
  fanza: 'FANZA 官方(CID)',
  dl_getchu: 'getchu 官方下载渠道（dl.getchu.com）',
  gyutto: 'gyutto 官方下载渠道（gyutto.com）',
}

/**
 * 预设: 一次性设定 normal+fc2+cid 三张清单的组合。
 * 全部进「未保存」态, 由用户点「保存并应用」才落盘。
 */
export const PRESETS = {
  recommend: {
    label: '推荐方案',
    desc: '覆盖主流来源, 新源兜底交叉验证',
    select: {
      normal: ['airav', 'avsox', 'javbus', 'javdb', 'javlib', 'jav321',
               'mgstage', 'prestige', 'javdbapi', 'javdatabase', 'javmenu', 'javfree'],
      fc2: ['fc2', 'avsox', 'javdb', 'javmenu', 'fc2ppvdb'],
      cid: ['fanza'],
    },
  },
  core: {
    label: '仅核心源',
    desc: '只保留最稳定的主力源, 最快',
    select: {
      normal: ['javdb', 'javbus', 'javlib', 'jav321', 'javdbapi', 'airav', 'javfree'],
      fc2: ['fc2', 'javdb', 'avsox'],
      cid: ['fanza'],
    },
  },
}
