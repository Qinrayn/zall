"""zall.core.builder — AgentBuilder fluent builder for AgentLoop construction.

Inspired by Grok Build's AgentBuilder pattern. Eliminates wiring duplication
between orchestator.run() and repl_ui.build_repl_loop() by providing a single
builder that both paths use.

v0.3.0: 新增 AgentDefinition 支持:
  - with_agent_definition(): 从 AgentDefinition 构建
  - with_agent_file(): 从 .zall/agents/*.md 文件构建
  - AgentDefinition 自动选择工具集预设、权限模式、模型等

Usage:
    loop = AgentBuilder() \\
        .with_model(adapter) \\
        .with_tools(tools) \\
        .with_rules(rules) \\
        .with_goal(goal) \\
        .with_context(context) \\
        .with_responder(responder) \\
        .with_judge(judge) \\
        .with_compactor(ModelCompactor()) \\
        .with_checkpoint(checkpoint_mgr) \\
        .with_git_protect(git_protect) \\
        .with_observer(observer) \\
        .with_extensions(ext_registry) \\
        .with_plan_mode(True) \\
        .build()

    # 或使用 AgentDefinition:
    loop = AgentBuilder() \\
        .with_agent_definition(agent_def) \\
        .with_model(adapter) \\
        ... \\
        .build()

Corresponds to:
  §9.2.1  Goal confirmation wiring
  §4.2    ToolRegistry construction
  §4.5    ConfirmGate + UserResponder
  §5.2    Judge
  §9.2.11 MCP tools lifecycle

IPR constraints:
  IPR-1: corresponds to DESIGN.md §4.2 + §4.5 + §5.2 + §9.2.1 + §9.2.11
  IPR-3: stdlib + pydantic only, no model SDK
"""

from __future__ import annotations

from typing import Any, Callable, Optional


class AgentBuilder:
    """Fluent builder for AgentLoop construction.

    Provides chainable .with_*() methods. Each method returns self.
    build() validates required fields and constructs the AgentLoop.

    Required fields:
      - model
      - tools (or agent_definition)
      - rules
      - goal
      - context
      - user_responder

    Optional fields (with defaults):
      - judge: None (UndecidableJudge)
      - observer: None
      - event_bus: None (created internally)
      - max_steps: None (no limit)
      - stream: False
      - git_protect: None
      - checkpoint_mgr: None
      - allow_downgrade: True
      - plan_mode: False
      - compactor: None
      - anchor: None
      - ext_registry: None
      - agent_definition: None
    """

    def __init__(self) -> None:
        self._model: Any = None
        self._tools: Any = None
        self._rules: Any = None
        self._goal: Any = None
        self._context: Any = None
        self._user_responder: Any = None

        # Optional fields
        self._judge: Any = None
        self._observer: Any = None
        self._event_bus: Any = None
        self._max_steps: int | None = None
        self._stream: bool = False
        self._git_protect: Any = None
        self._checkpoint_mgr: Any = None
        self._allow_downgrade: bool = True
        self._plan_mode: bool = False
        self._compactor: Any = None
        self._anchor: Any = None
        self._ext_registry: Any = None
        self._agent_definition: Any = None

    # ═══════════════════════════════════════════════════════════════
    # Required fields
    # ═══════════════════════════════════════════════════════════════

    def with_model(self, model: Any) -> AgentBuilder:
        """Set the ModelAdapter."""
        self._model = model
        return self

    def with_tools(self, tools: Any) -> AgentBuilder:
        """Set the ToolRegistry."""
        self._tools = tools
        return self

    def with_rules(self, rules: Any) -> AgentBuilder:
        """Set the RuleSet."""
        self._rules = rules
        return self

    def with_goal(self, goal: Any) -> AgentBuilder:
        """Set the GoalTriple."""
        self._goal = goal
        return self

    def with_context(self, context: Any) -> AgentBuilder:
        """Set the Context."""
        self._context = context
        return self

    def with_responder(self, responder: Any) -> AgentBuilder:
        """Set the UserResponder."""
        self._user_responder = responder
        return self

    # ═══════════════════════════════════════════════════════════════
    # AgentDefinition support (v0.3.0)
    # ═══════════════════════════════════════════════════════════════

    def with_agent_definition(self, agent_def: Any) -> AgentBuilder:
        """Set the AgentDefinition.

        设置后, build() 会从 AgentDefinition 自动提取工具集、权限模式等。
        如果未单独设置 plan_mode, 会根据 AgentDefinition 的 permission_mode 自动设置。
        """
        self._agent_definition = agent_def
        return self

    def with_agent_file(self, path: str) -> AgentBuilder:
        """从 .md 文件加载 AgentDefinition。"""
        from zall.core.agent import AgentDefinition
        agent_def = AgentDefinition.from_file(path)
        self._agent_definition = agent_def
        return self

    # ═══════════════════════════════════════════════════════════════
    # Optional fields
    # ═══════════════════════════════════════════════════════════════

    def with_judge(self, judge: Any) -> AgentBuilder:
        """Set the Judge."""
        self._judge = judge
        return self

    def with_observer(self, observer: Callable | None) -> AgentBuilder:
        """Set the observer callable (receives LoopEvent)."""
        self._observer = observer
        return self

    def with_event_bus(self, event_bus: Any) -> AgentBuilder:
        """Set the EventBus (overrides default)."""
        self._event_bus = event_bus
        return self

    def with_max_steps(self, max_steps: int | None) -> AgentBuilder:
        """Set the maximum step count."""
        self._max_steps = max_steps
        return self

    def with_stream(self, stream: bool) -> AgentBuilder:
        """Enable streaming mode."""
        self._stream = stream
        return self

    def with_git_protect(self, git_protect: Any) -> AgentBuilder:
        """Set the GitProtect safety net."""
        self._git_protect = git_protect
        return self

    def with_checkpoint(self, checkpoint_mgr: Any) -> AgentBuilder:
        """Set the CheckpointManager."""
        self._checkpoint_mgr = checkpoint_mgr
        return self

    def with_allow_downgrade(self, allow: bool) -> AgentBuilder:
        """Allow Goal downgrade."""
        self._allow_downgrade = allow
        return self

    def with_plan_mode(self, plan_mode: bool) -> AgentBuilder:
        """Enable plan (read-only) mode."""
        self._plan_mode = plan_mode
        return self

    def with_compactor(self, compactor: Any) -> AgentBuilder:
        """Set the Compactor (e.g., ModelCompactor)."""
        self._compactor = compactor
        return self

    def with_anchor(self, anchor: Any) -> AgentBuilder:
        """Set the TrustAnchor."""
        self._anchor = anchor
        return self

    def with_extensions(self, ext_registry: Any) -> AgentBuilder:
        """Set the ExtensionRegistry."""
        self._ext_registry = ext_registry
        return self

    # ═══════════════════════════════════════════════════════════════
    # Build
    # ═══════════════════════════════════════════════════════════════

    def build(self) -> Any:
        """Validate required fields and construct AgentLoop.

        Returns:
            An AgentLoop instance.

        Raises:
            ValueError: If any required field is missing.
        """
        # 如果设置了 AgentDefinition, 应用其配置
        self._apply_agent_definition()

        self._validate()

        from zall.core.loop import AgentLoop, AgentConfig

        config = AgentConfig(
            judge=self._judge,
            observer=self._observer,
            event_bus=self._event_bus,
            max_steps=self._max_steps,
            stream=self._stream,
            git_protect=self._git_protect,
            checkpoint_mgr=self._checkpoint_mgr,
            allow_downgrade=self._allow_downgrade,
            plan_mode=self._plan_mode,
            compactor=self._compactor,
            anchor=self._anchor,
            ext_registry=self._ext_registry,
        )

        return AgentLoop(
            model=self._model,
            tools=self._tools,
            rules=self._rules,
            goal=self._goal,
            context=self._context,
            user_responder=self._user_responder,
            config=config,
        )

    def _apply_agent_definition(self) -> None:
        """从 AgentDefinition 应用配置到 builder。

        如果 AgentDefinition 设定了 toolset, 则使用预设构建工具集。
        如果设定了 permission_mode/plan_mode, 自动设置 plan_mode。
        如果设定了 model, 可覆盖模型选择。
        """
        if self._agent_definition is None:
            return

        ad = self._agent_definition

        # 如果未手动设置 tools, 从 AgentDefinition 构建
        if self._tools is None:
            from zall.core.toolset import build_native_tools_for_preset
            tool_list = build_native_tools_for_preset(ad.toolset.value)
            from zall.core.tool import ToolRegistry
            self._tools = ToolRegistry(tools=tuple(tool_list))

        # 根据 permission_mode 设置 plan_mode
        if ad.permission_mode.value == "plan":
            self._plan_mode = True

        # 如果 AgentDefinition 有 disallowed_tools, 过滤工具
        if ad.disallowed_tools and self._tools is not None:
            filtered = [
                t for t in self._tools.tools
                if t.tool_id not in ad.disallowed_tools
            ]
            from zall.core.tool import ToolRegistry
            self._tools = ToolRegistry(tools=tuple(filtered))

        # 如果 AgentDefinition 有 tools allowlist, 只保留这些
        if ad.tools and self._tools is not None:
            filtered = [
                t for t in self._tools.tools
                if t.tool_id in ad.tools
            ]
            from zall.core.tool import ToolRegistry
            self._tools = ToolRegistry(tools=tuple(filtered))

    def _validate(self) -> None:
        """Check that all required fields are set.

        Required: model, tools, rules, goal, context, user_responder.
        """
        missing: list[str] = []
        if self._model is None:
            missing.append("model")
        if self._tools is None:
            missing.append("tools")
        if self._rules is None:
            missing.append("rules")
        if self._goal is None:
            missing.append("goal")
        if self._context is None:
            missing.append("context")
        if self._user_responder is None:
            missing.append("user_responder")

        if missing:
            raise ValueError(
                f"AgentBuilder missing required fields: {', '.join(missing)}. "
                f"Use .with_model(), .with_tools(), etc. to set them."
            )


def build_loop_minimal(
    model: Any,
    tools: Any,
    rules: Any,
    goal: Any,
    context: Any,
    responder: Any,
    **kwargs: Any,
) -> Any:
    """Convenience function: single-call AgentLoop construction.

    Usage:
        loop = build_loop_minimal(model, tools, rules, goal, context, responder,
                                   judge=judge, compactor=ModelCompactor())

    This is equivalent to:
        AgentBuilder() \\
            .with_model(model) \\
            .with_tools(tools) \\
            ... \\
            .build()

    Useful for simple cases where chaining is overkill.
    """
    builder = AgentBuilder()
    builder.with_model(model)
    builder.with_tools(tools)
    builder.with_rules(rules)
    builder.with_goal(goal)
    builder.with_context(context)
    builder.with_responder(responder)

    # Optional kwargs
    for key, val in kwargs.items():
        method_name = f"with_{key}"
        if hasattr(builder, method_name):
            getattr(builder, method_name)(val)

    return builder.build()


def build_from_agent_definition(
    agent_def: Any,
    model: Any,
    rules: Any,
    goal: Any,
    context: Any,
    responder: Any,
    **kwargs: Any,
) -> Any:
    """从 AgentDefinition 构建 AgentLoop。

    Usage:
        loop = build_from_agent_definition(
            agent_def, model, rules, goal, context, responder,
            judge=judge, compactor=ModelCompactor(),
        )
    """
    builder = AgentBuilder()
    builder.with_agent_definition(agent_def)
    builder.with_model(model)
    builder.with_rules(rules)
    builder.with_goal(goal)
    builder.with_context(context)
    builder.with_responder(responder)

    for key, val in kwargs.items():
        method_name = f"with_{key}"
        if hasattr(builder, method_name):
            getattr(builder, method_name)(val)

    return builder.build()