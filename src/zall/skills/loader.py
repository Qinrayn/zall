"""zall.skills.loader — skill load (.zall/skills.toml).

DESIGN §9.2.7 (斜杠命令 → 输入快捷方式) + §9.4 (skills = Goal templates)。

skill 是**可复用的 Goal 模板** (预填 prompt), 不是"免确认的宏":
  用户输入 /skill <name> [args] → 展开为 task 文本 → 走完整 Goal 锁定 + ConfirmGate。
  若展开后的任务触发 greylist/blacklist 动作, 仍走 context_judge + confirm_gate
  (§9.2.7 偷渡防线: 斜杠命令不绕 gate)。

极简 TOML 格式 (与 mcp.toml / rules.toml 同源哲学, IPR-3 仅 stdlib):

    [[skills]]
    name = "review"
    description = "review current diff for bugs and regressions"
    prompt = "Review the current git diff. Report findings by severity."

    [[skills]]
    name = "explain"
    description = "explain a file's purpose and structure"
    prompt = \"\"\"
    Read {input} and explain its purpose, structure, and key functions.
    \"\"\"

占位符:
  {input} —— 调用时传入的参数 (/skill explain src/foo.py → input="src/foo.py")。
  若 prompt 无 {input} 但调用带参 → 参数附加到 prompt 末尾。

优先级: 项目级 .zall/skills.toml > 用户级 ~/.zall/skills.toml (同名后者覆盖)。
无配置 / 解析失败 → 返回 [] (失败安全 IPR-0, 不阻断 agent 启动)。

IPR constraints:
  IPR-3: 仅 stdlib (手写极简 [[skills]] 解析, 含多行 \"\"\" prompt, 不引 toml 库)
  IPR-0: 文件缺失 / 编码错误 / 解析错误都不得让 agent 启动崩溃
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zall._util.toml import unquote_value as _unquote

_INPUT_PLACEHOLDER = "{input}"


@dataclass(frozen=True)
class Skill:
    """一个可复用工作stream声明 (§9.4 Goal template)。"""

    name: str
    description: str
    prompt: str

    def expand(self, arg: str = "") -> str:
        """把 skill 展开为commit给 agent 的 task 文本。

        - prompt 含 {input} → 用 arg 替换 (arg 为空则替换成空串)。
        - prompt 无 {input} 但 arg 非空 → arg 附加到 prompt 末尾。
        - 否则 → 原样返回 prompt。

        展开结果作为普通用户输入, 仍走 Refiner → Goal 锁定 → ConfirmGate
        (§9.2.7 不绕 gate; R1 翻译禁加戏: 用户显式调 skill = 显式提供此意图)。
        """
        arg = (arg or "").strip()
        if _INPUT_PLACEHOLDER in self.prompt:
            return self.prompt.replace(_INPUT_PLACEHOLDER, arg).strip()
        if arg:
            return f"{self.prompt.strip()}\n\n{arg}"
        return self.prompt.strip()


def load_skills(
    user_path: str | None = None,
    project_path: str | None = None,
) -> list[Skill]:
    """load skill 声明 (项目级覆盖用户级同名)。失败security → 最坏return []。"""
    project = (
        _load_one(Path(project_path) / ".zall" / "skills.toml")
        if project_path
        else _load_one(Path.cwd() / ".zall" / "skills.toml")
    )
    user = (
        _load_one(Path(user_path))
        if user_path
        else _load_one(Path.home() / ".zall" / "skills.toml")
    )
    merged: dict[str, Skill] = {}
    for skill in user:
        merged[skill.name] = skill
    for skill in project:
        merged[skill.name] = skill  # 项目级优先
    return list(merged.values())


def find_skill(skills: list[Skill], name: str) -> Skill | None:
    """按名find skill (大小写不敏感, ignore前导 /)。"""
    target = name.strip().lstrip("/").lower()
    for skill in skills:
        if skill.name.lower() == target:
            return skill
    return None


def _load_one(path: Path) -> list[Skill]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        return _parse_skills(text)
    except Exception:
        # 失败security: parseexception → skip该file, 不阻断 agent 启动 (IPR-0)
        return []


def _parse_skills(text: str) -> list[Skill]:
    """parse极简 [[skills]] TOML (name/description/prompt, prompt 支持多行 \"\"\")。"""
    skills: list[Skill] = []
    current: dict[str, Any] | None = None
    # 多行字符串state: 当遇到 key = \"\"\" 未闭合, 进入收集直到出现 \"\"\"
    in_multiline = False
    ml_key = ""
    ml_lines: list[str] = []

    for raw_line in text.split("\n"):
        if in_multiline:
            stripped = raw_line.strip()
            if stripped.endswith('"""'):
                # 末行去掉收尾 """; 若该行有前缀content也preserve
                tail = stripped[:-3]
                if tail:
                    ml_lines.append(tail)
                if current is not None:
                    current[ml_key] = "\n".join(ml_lines).strip()
                in_multiline = False
                ml_key = ""
                ml_lines = []
            else:
                ml_lines.append(raw_line)
            continue

        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line == "[[skills]]":
            if current:
                _emit(skills, current)
            current = {}
            continue
        if current is None:
            continue

        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()

        # 多行起始: key = \"\"\" (可能同行闭合)
        if val.startswith('"""'):
            rest = val[3:]
            if rest.endswith('"""') and len(rest) >= 3:
                current[key] = rest[:-3].strip()
            else:
                in_multiline = True
                ml_key = key
                ml_lines = [rest] if rest else []
            continue

        current[key] = _unquote(val)

    if current:
        _emit(skills, current)
    return skills


def _emit(skills: list[Skill], d: dict[str, Any]) -> None:
    """把一个parse出的 skill dict 追加进list; 无效 (None) 则skip。"""
    skill = _skill_from(d)
    if skill is not None:
        skills.append(skill)


def _skill_from(d: dict[str, Any]) -> Skill | None:
    """从parse字典construct Skill; 缺 name 或 prompt → return None (该 skill skip)。

    不抛异常: 单个 skill 非法只跳过它, 不污染同文件其它合法 skill
    (整文件级失败安全由 _load_one 的 try/except 兜底, 应对真正坏 TOML)。
    """
    name = (d.get("name") or "").strip()
    prompt = (d.get("prompt") or "").strip()
    if not name or not prompt:
        return None
    return Skill(
        name=name,
        description=(d.get("description") or "").strip(),
        prompt=prompt,
    )
