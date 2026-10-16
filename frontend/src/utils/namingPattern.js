/**
 * 命名规则 解析/序列化/校验/预览
 * ---------------------------------------------------------------------------
 * 与后端 core.py 的契约(改这里必须同步改后端, 反之亦然):
 *   - 后端用 str.format(**copyd) 渲染, 占位符是 {var} 花括号风格;
 *   - copyd 的键共 14 个(上游 13 + fork 独有 {genre}), 见 datatype.py get_info_dic;
 *   - 多分片影片 copyd['num'] 会自动追加 -CDx 后缀;
 *   - 模板里出现未知占位符会 KeyError 直接炸整理 —— 所以前端必须校验。
 *
 * 本模块是纯函数, 不依赖 Vue/DOM, 可单测。
 */

/** 14 个可用变量: 源码名 ←→ 中文名 ←→ 说明 ←→ 预览示例值 */
export const NAMING_VARS = [
  { key: 'num',      label: '番号',       desc: '影片番号',        sample: 'IPX-177' },
  { key: 'title',    label: '标题',       desc: '影片标题',        sample: '示例影片标题' },
  { key: 'rawtitle', label: '原始标题',   desc: '未翻译的原始标题', sample: 'サンプル動画タイトル' },
  { key: 'actress',  label: '女优',       desc: '女优名',          sample: '波多野结衣' },
  { key: 'score',    label: '评分',       desc: '网站评分',        sample: '8.7' },
  { key: 'censor',   label: '有码/无码',  desc: '骑兵/步兵标识',    sample: '无码' },
  { key: 'serial',   label: '系列',       desc: '系列名',          sample: '示例系列' },
  { key: 'director', label: '导演',       desc: '导演名',          sample: '示例导演' },
  { key: 'producer', label: '制作商',     desc: '制作商',          sample: '示例制作商' },
  { key: 'publisher',label: '发行商',     desc: '发行商',          sample: '示例发行商' },
  { key: 'date',     label: '发行日期',   desc: '发行日期',        sample: '2024-01-15' },
  { key: 'year',     label: '年份',       desc: '发行年份',        sample: '2024' },
  { key: 'label',    label: '厂牌',       desc: '多番号时取首个',   sample: '示例厂牌' },
  { key: 'genre',    label: '类型',       desc: '影片类型(fork 扩展)', sample: '剧情' },
]

export const VAR_KEYS = NAMING_VARS.map((v) => v.key)

const SAMPLE_BY_KEY = Object.fromEntries(NAMING_VARS.map((v) => [v.key, v.sample]))

/** 常用符号: 目录/文件名里的高频分隔符 */
export const COMMON_SYMBOLS = [' ', '-', '.', '_', '(', ')', '[', ']']

/* ---------- 结构 ↔ 源码 双向转换 ---------- */

/**
 * 模板字符串 → 层数组(可视化构建器的内部结构)
 *   '番号/{num}/{actress}' → [[{var:num}], [{var:num}], [{var:actress}]]
 * 每个 token: { type: 'var'|'text', value: 'num' }
 * 文本 token 保留用户手打的一切字符(含未知花括号内容)。
 */
export function parsePattern(pattern) {
  const src = typeof pattern === 'string' ? pattern : ''
  return src.split('/').map((seg) => tokenizeSegment(seg))
}

/**
 * 单段(不含 / 分隔)切分为 token 流。
 * single 模式(文件名/NFO 模板)用这个 —— 整个字符串当作一层, / 也是普通字符。
 */
export function tokenizeSegment(seg) {
  const tokens = []
  const re = /\{(\w+)\}/g
  let last = 0
  let m
  while ((m = re.exec(seg)) !== null) {
    if (m.index > last) tokens.push({ type: 'text', value: seg.slice(last, m.index) })
    tokens.push({ type: 'var', value: m[1] })
    last = m.index + m[0].length
  }
  if (last < seg.length) tokens.push({ type: 'text', value: seg.slice(last) })
  return tokens
}

/** 层数组 → 模板字符串(层间 / join, 层内 token 拼接) */
export function serializeLayers(layers) {
  return layers.map((layer) => layer.map(tokenToSource).join('')).join('/')
}

function tokenToSource(t) {
  if (!t) return ''
  if (t.type === 'var') return `{${t.value}}`
  return t.value || ''
}

/* ---------- 校验 ---------- */

/** 找出模板中不存在的变量名(后端渲染时会 KeyError 的元凶) */
export function findUnknownVars(pattern, knownKeys = VAR_KEYS) {
  const unknown = []
  if (typeof pattern !== 'string') return unknown
  const re = /\{(\w+)\}/g
  let m
  while ((m = re.exec(pattern)) !== null) {
    if (!knownKeys.includes(m[1]) && !unknown.includes(m[1])) unknown.push(m[1])
  }
  return unknown
}

/** 单层是否为空(空目录名/空文件名是隐性 bug) */
export function hasEmptyLayer(layers) {
  return layers.some((layer) => layer.length === 0 || serializeLayers([layer]).trim() === '')
}

/* ---------- 预览 ---------- */

/**
 * 用示例数据渲染模板(模拟后端 str.format 的效果, 但未知变量原样保留
 * —— 让用户在预览里直接看见 {xxx}, 而不是等整理时报错)。
 */
export function renderPattern(pattern) {
  if (typeof pattern !== 'string') return ''
  return pattern.replace(/\{(\w+)\}/g, (whole, key) =>
    key in SAMPLE_BY_KEY ? SAMPLE_BY_KEY[key] : whole
  )
}

/** 长度测量: byByte 时按 UTF-8 字节算(中文 3 字节), 与 length_by_byte 配置对应 */
export function measureLength(text, byByte) {
  if (typeof text !== 'string') return 0
  if (!byByte) return text.length
  let bytes = 0
  for (const ch of text) {
    const code = ch.codePointAt(0)
    if (code <= 0x7f) bytes += 1
    else if (code <= 0x7ff) bytes += 2
    else bytes += 3
  }
  return bytes
}

/* ---------- 快速模板 ---------- */

export const PATTERN_PRESETS = [
  { name: '默认结构', pattern: '{num}/{actress}/{num} {title}' },
  { name: '按发行商归档', pattern: '{publisher}/{num}/{num} {title}' },
  { name: '按系列归档', pattern: '{serial}/{num}/{num} {title}' },
  { name: '按日期归档', pattern: '{year}/{date}/{num} {title}' },
]
