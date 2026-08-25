"""Task 分类器: bug 类型和 domain 领域推断."""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.parser.patch_parser import parse_patch


@dataclass
class TaskClassification:
    """单个 instance 的 bug 类型和 domain 分类."""

    instance_id: str
    bug_type: str  # bug 类型标签
    domain: str  # 代码领域标签
    sub_domain: str  # 子领域 (如 repo 下的模块路径)


# ---------------------------------------------------------------------------
# Bug 类型分类
# ---------------------------------------------------------------------------

# bug 类型: 基于修改模式 (patch 行内容) 和 problem_statement 关键词推断
_BUG_TYPE_RULES: list[tuple[str, re.Pattern, re.Pattern]] = [
    # (bug_type, problem_statement_pattern, patch_pattern)
    # 优先级从高到低
    (
        "api_misuse",
        re.compile(
            r"\b(wrong (result|output|return|behavior)|incorrect (value|result|output)|unexpected (result|value|return))\b",
            re.I,
        ),
        re.compile(r"^\+\s*return\s|^\-\s*return\s", re.M),
    ),
    (
        "logic_error",
        re.compile(
            r"\b(logic(al)? (error|bug|flaw|mistake)|incorrect (logic|condition|calculation|behavior)|wrong (logic|condition|calculation|behavior))\b",
            re.I,
        ),
        re.compile(
            r"^\+\s*if\s|^\-\s*if\s|^\+\s*elif\s|^\-\s*elif\s|^\+\s*else|^\-\s*else",
            re.M,
        ),
    ),
    (
        "off_by_one",
        re.compile(
            r"\b(off.by.one|off by one|fence.post|boundary|index (error|out of)|IndexError|out of (range|bounds))\b",
            re.I,
        ),
        re.compile(
            r"[\+\-].*[\[\(].*[\+\-]\s*\d+|[\+\-].*range\s*\(|[\+\-].*\bindex\b", re.M
        ),
    ),
    (
        "type_error",
        re.compile(
            r"\b(TypeError|type (error|mismatch|conversion|annotation|check))\b", re.I
        ),
        re.compile(
            r"^\+\s*(int|float|str|bool|list|dict|tuple|set|bytes)\s*\(|^\+\s*isinstance\s*\(|^\+\s*typing\.",
            re.M,
        ),
    ),
    (
        "attribute_error",
        re.compile(r"\bAttributeError\b", re.I),
        re.compile(r"^\+\s*self\.\w+\s*=|^\-\s*self\.\w+", re.M),
    ),
    (
        "key_error",
        re.compile(r"\bKeyError\b", re.I),
        re.compile(r"[\+\-].*\[(['\"]).+\\1\]", re.M),
    ),
    (
        "import_error",
        re.compile(
            r"\b(ImportError|ModuleNotFoundError|import (error|cycle|circular))\b", re.I
        ),
        re.compile(r"^\+\s*(import |from )|^\-\s*(import |from )", re.M),
    ),
    (
        "null_pointer",
        re.compile(
            r"\b(NoneType|'NoneType'|null pointer|NoneType (has no|object)|AttributeError.*None)",
            re.I,
        ),
        re.compile(
            r"^\+\s*if\s+.*\bis\s+None|^\+\s*if\s+.*\bis\s+not\s+None|^\+\s*or\s+None|^\+\s*and\s+None",
            re.M,
        ),
    ),
    (
        "race_condition",
        re.compile(
            r"\b(race.condition|concurrent|thread.safety|deadlock|thread.safe|mutex|lock)\b",
            re.I,
        ),
        re.compile(r"^\+\s*(with\s+.*lock|threading\.|asyncio\.|Lock\(\))", re.M),
    ),
    (
        "memory_leak",
        re.compile(
            r"\b(memory.leak|resource.leak|file (descriptor|handle) leak|unclosed)",
            re.I,
        ),
        re.compile(
            r"^\+\s*(with\s+open|\.close\(\)|contextlib|__del__|__exit__)", re.M
        ),
    ),
    (
        "encoding_error",
        re.compile(
            r"\b(Unicode(Decode|Encode)Error|encoding|decode error|charset|codec)", re.I
        ),
        re.compile(r"^\+\s*\.(encode|decode)\s*\(|^\+\s*encoding\s*=", re.M),
    ),
    (
        "deprecation",
        re.compile(
            r"\b(deprecat|removed in|no longer (supported|available)|legacy|migrated)\b",
            re.I,
        ),
        re.compile(r"^\+\s*#.*deprecat|^\-\s*#.*deprecat|^\+\s*warnings\.warn", re.M),
    ),
    (
        "configuration",
        re.compile(
            r"\b(config|setting|option|preference|environment variable|env var|\.cfg|\.ini|\.conf)\b",
            re.I,
        ),
        re.compile(r"^\+\s*(config|settings|os\.environ|CONFIG_|DEFAULT_)", re.M),
    ),
    (
        "documentation",
        re.compile(
            r"\b(docstring|documentation|doc string|help text|sphinx|readthedocs)\b",
            re.I,
        ),
        re.compile(r"^\+\s*(\"\"\"|\'\'\'|#:|:param|:return|:raises)", re.M),
    ),
]

# 兜底: 纯 patch 模式匹配 (不需要 problem_statement 匹配)
_PATCH_ONLY_RULES: list[tuple[str, re.Pattern]] = [
    ("api_misuse", re.compile(r"^\+\s*return\s|^\-\s*return\s", re.M)),
    (
        "logic_error",
        re.compile(r"^\+\s*if\s|^\-\s*if\s|^\+\s*elif\s|^\-\s*elif\s", re.M),
    ),
    ("import_error", re.compile(r"^\+\s*(import |from )|^\-\s*(import |from )", re.M)),
    (
        "type_error",
        re.compile(
            r"^\+\s*(int|float|str|bool|list|dict)\s*\(|^\+\s*isinstance\s*\(", re.M
        ),
    ),
    ("null_pointer", re.compile(r"^\+\s*if\s+.*\bis\s+(not\s+)?None", re.M)),
]


def classify_bug_type(problem_statement: str, patch_text: str) -> str:
    """基于 problem_statement + patch 内容推断 bug 类型.

    优先使用 problem+patch 双重匹配, 其次仅 patch 匹配, 兜底返回 'other'.
    """
    # 第一轮: problem + patch 双重匹配
    for bug_type, prob_re, patch_re in _BUG_TYPE_RULES:
        if prob_re.search(problem_statement) and patch_re.search(patch_text):
            return bug_type

    # 第二轮: 仅 patch 匹配
    for bug_type, patch_re in _PATCH_ONLY_RULES:
        if patch_re.search(patch_text):
            return bug_type

    # 第三轮: 仅 problem_statement 匹配 (放宽条件)
    for bug_type, prob_re, _ in _BUG_TYPE_RULES:
        if prob_re.search(problem_statement):
            return bug_type

    return "other"


# ---------------------------------------------------------------------------
# Domain 分类
# ---------------------------------------------------------------------------

# repo -> domain 映射
_REPO_DOMAIN_MAP: dict[str, str] = {
    "django/django": "web_framework",
    "sympy/sympy": "scientific_computing",
    "sphinx-doc/sphinx": "documentation_tool",
    "matplotlib/matplotlib": "visualization",
    "scikit-learn/scikit-learn": "machine_learning",
    "astropy/astropy": "scientific_computing",
    "pydata/xarray": "scientific_computing",
    "pytest-dev/pytest": "testing_framework",
    "pylint-dev/pylint": "static_analysis",
    "psf/requests": "networking",
    "mwaskom/seaborn": "visualization",
    "pallets/flask": "web_framework",
}

# 通用子领域推断: 基于修改文件路径中的关键词
_SUB_DOMAIN_KEYWORDS: list[tuple[str, str]] = [
    ("test", "testing"),
    ("model", "orm"),
    ("view", "views"),
    ("url", "routing"),
    ("template", "templates"),
    ("admin", "admin"),
    ("auth", "auth"),
    ("form", "forms"),
    ("serializer", "serialization"),
    ("api", "api"),
    ("cache", "caching"),
    ("db", "database"),
    ("sql", "database"),
    ("migration", "database"),
    ("middleware", "middleware"),
    ("signal", "signals"),
    ("core", "core"),
    ("contrib", "contrib"),
    ("plot", "plotting"),
    ("figure", "plotting"),
    ("axes", "plotting"),
    ("symbol", "symbolic"),
    ("expr", "symbolic"),
    ("solvers", "solvers"),
    ("io", "io"),
    ("parser", "parsing"),
    ("lexer", "parsing"),
    ("lint", "linting"),
    ("check", "checking"),
    ("config", "configuration"),
    ("cli", "cli"),
    ("command", "cli"),
    ("http", "http"),
    ("request", "http"),
    ("session", "session"),
    ("cookie", "session"),
    ("crypto", "crypto"),
    ("hash", "crypto"),
    ("unicode", "text"),
    ("encoding", "text"),
    ("format", "formatting"),
    ("date", "datetime"),
    ("time", "datetime"),
    ("timezone", "datetime"),
]


def classify_domain(repo: str, file_paths: set[str]) -> tuple[str, str]:
    """推断 instance 的领域和子领域.

    Returns: (domain, sub_domain)
    """
    domain = _REPO_DOMAIN_MAP.get(repo, "other")

    # 从修改文件路径推断子领域
    sub_domain = "general"
    all_paths = " ".join(sorted(file_paths)).lower()

    for keyword, sub in _SUB_DOMAIN_KEYWORDS:
        if keyword in all_paths:
            sub_domain = sub
            break

    return domain, sub_domain


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def classify_task(
    instance_id: str,
    repo: str,
    problem_statement: str,
    gold_patch_text: str,
) -> TaskClassification:
    """对单个 instance 进行 bug 类型和 domain 分类."""
    gold_info = parse_patch(gold_patch_text)
    bug_type = classify_bug_type(problem_statement, gold_patch_text)
    domain, sub_domain = classify_domain(repo, gold_info.file_paths)

    return TaskClassification(
        instance_id=instance_id,
        bug_type=bug_type,
        domain=domain,
        sub_domain=sub_domain,
    )
