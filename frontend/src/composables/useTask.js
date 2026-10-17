/**
 * 全局任务进度(供底部状态栏常驻显示)
 * ---------------------------------------------------------------------------
 * 为什么需要: 批量刮削/批量整理都是分钟级操作, 旧版进度只活在各自页面里 ——
 * 用户一旦切到别的页面(如去影片库确认信息), 就完全看不到任务跑到哪了。
 * 状态栏常驻进度是 App.vue 设计意图里的第 3 点("状态常驻"), 这里补上数据通路。
 *
 * 设计取舍:
 *   - 模块级 reactive 单例: 各页面 import 即共享同一份状态, 不需要事件总线或
 *     store 库; 页面卸载后状态仍在(任务本就与页面无关)。
 *   - 只存最小进度三元组(index/total/current): 不复制业务数据, 避免与页面
 *     自身的 log/movies 状态重复导致不一致。
 *   - start/finish 必须成对: 中断(用户取消)或异常时 finally 里也要 finish,
 *     否则状态栏会卡在一个永不结束的假进度上。
 */
import { reactive } from 'vue'

const state = reactive({
  running: false,
  index: 0,
  total: 0,
  current: '',
})

export function useTask() {
  return {
    task: state,
    taskStart(total) {
      state.running = true
      state.index = 0
      state.total = total || 0
      state.current = ''
    },
    taskProgress(index, current) {
      state.index = index || 0
      if (current !== undefined) state.current = current || ''
    },
    taskFinish() {
      state.running = false
      state.index = 0
      state.total = 0
      state.current = ''
    },
  }
}
