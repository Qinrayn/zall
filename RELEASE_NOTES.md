# zall — Release Notes (0.6.2)

0.6.1 → 0.6.2 的变更。最大变化是安装：不写代码也可以「下载即用」，全程
不碰 Python。无破坏性变更，配置文件格式不变。技术细节见 `CHANGELOG.md`。

## 安装（新版三选一）

1. **下载即用（推荐）**：到
   [Releases](https://github.com/qinrayn/zall/releases) 下载 `zall.exe`，
   放到任意文件夹（或桌面）双击运行 — 单文件自包含，Windows 10/11 x64
   不需要安装 Python，也不需要命令行知识。
2. **一条命令**：`uv tool install zall`。装好
   [uv](https://docs.astral.sh/uv/) 后它会自动准备 Python 环境；想先试用
   不安装可以用 `uvx zall --version`。
3. **pip**：`pip install -U zall`（已有 Python 环境的老路径）。

## 变更

- **REPL 双击 Ctrl+C 退出**：单次 Ctrl+C 打断当前输入（清行继续，并提示
  "Ctrl-C again to exit"），2 秒内第二次退出，退出码 130。
- **一个回合出错不再拖垮会话**：回合里出现未预期错误时只落一行
  `✗ internal error` 并回到提示符，会话上下文原样保留，可以直接继续或换
  模型重试。
- **启动期 Ctrl+C / 管道断裂安静退出**：不再打印 Python traceback
  （`zall --help | head` 这类用法也干净）。
- **对话行看得更清楚**：提示符上的模型名与 `▸` 用主题强调色加粗（原来是
  暗灰），`[plan]` 单独琥珀色；启动屏只保留一行提示，常用键位常驻显示在
  提示符下方的状态行里，不再重复堆叠。
- **`/model` 标注上游真实状态**：当前网关会显示上游 `/models` 返回的可用
  模型数量；配置里有、但上游已经不提供的模型会标 ⚠，避免切过去才撞 404。
- **exe 形态的 `/update`**：照常提示有新版本，但改为引导去 Releases 下载
  新版 `zall.exe`（exe 不能自升级）。

## 修复

- **首个回合之后 REPL 可能冻结**：跑完任意一个任务后，再执行任何斜杠命令
  （实测 `/btw`、`/model`）或按空回车会卡住界面、只能杀进程重启 — 渲染器
  写锁自死锁，自 0.1.0 起存在（0.6.1 已发布代码同样携带）。已修复。
- `/model` 选择器上挂着任务输入框的提示文案（"Type a task…"），属误导；
  选择器与向导类子提示不再显示它。

## 升级

```
pip install -U zall
```

Windows 免 Python 用户：直接下载新的 `zall.exe` 替换旧文件即可，配置与
会话都存放在 `~/.zall`，不受影响。