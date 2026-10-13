from dataclasses import dataclass
from pathlib import Path

from app.core.errors import PromptNotFoundError
from app.ez.catalog import ACCOUNT_CATEGORY_LABEL, EzCatalog


@dataclass(frozen=True)
class PromptPair:
    system: str
    user: str


def load_prompt(prompts_dir: str, filename: str) -> str:
    path = Path(prompts_dir) / filename
    if not path.is_file():
        raise PromptNotFoundError(f"提示词文件不存在: {path}")
    return path.read_text(encoding="utf-8")


def catalog_prompt_lines(catalog: EzCatalog) -> str:
    account_lines = [
        (
            f"- {a.path} | 币种={a.currency} | "
            f"账户类别={ACCOUNT_CATEGORY_LABEL.get(a.category, str(a.category))} | id={a.id}"
        )
        for a in catalog.accounts
    ]
    category_lines = [
        f"- {c.path} | 描述={c.comment or '（无）'} | id={c.id}" for c in catalog.categories
    ]
    tag_lines = [f"- {t.name} | id={t.id}" for t in catalog.tags]
    return (
        "【可用账户】\n"
        + "\n".join(account_lines)
        + "\n\n【可用二级分类】\n"
        + "\n".join(category_lines)
        + "\n\n【可用标签】\n"
        + "\n".join(tag_lines if tag_lines else ["- （无可用标签）"])
    )


class PromptBuilder:
    """每次调用都重新读取提示词文件，修改挂载目录下的文件即可在下一次请求生效。"""

    def __init__(self, prompts_dir: str) -> None:
        self._prompts_dir = prompts_dir

    def load(self, filename: str) -> str:
        return load_prompt(self._prompts_dir, filename)

    def build(self, filename: str, catalog: EzCatalog, instruction: str) -> PromptPair:
        system = self.load(filename)
        user = catalog_prompt_lines(catalog) + "\n\n" + instruction
        return PromptPair(system=system, user=user)
