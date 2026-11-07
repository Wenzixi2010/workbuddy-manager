"""Runtime Codex prompt stays in the server Docker image (no Docker required)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROMPT = 'server/services/codex_catalog/prompt.md'


class CodexCatalogPackagingTest(unittest.TestCase):
    def test_runtime_prompt_and_notices_exist(self) -> None:
        folder = ROOT / 'server/services/codex_catalog'
        for name in ('prompt.md', 'LICENSE.txt', 'NOTICE.txt'):
            with self.subTest(name=name):
                self.assertTrue((folder / name).read_text(encoding='utf-8').strip(),
                                f'Codex 运行时资产缺失或为空：{name}')

    def test_runtime_prompt_has_explicit_docker_context_exception(self) -> None:
        rules = [line.strip() for line in (ROOT / '.dockerignore').read_text(
            encoding='utf-8').splitlines()
                 if line.strip() and not line.lstrip().startswith('#')]
        include = '!' + PROMPT
        self.assertIn(include, rules, '运行时 prompt 必须显式保留在 Docker 构建上下文')
        # This is an explicit whitelist/order guard, not a Docker glob matcher.
        # Moby's slashless *.md is root-relative; it does not itself exclude
        # this nested prompt. Keep the exception as defensive documentation.
        for markdown_rule in ('*.md', '**/*.md'):
            if markdown_rule in rules:
                self.assertGreater(rules.index(include), rules.index(markdown_rule))

    def test_dockerfile_copies_server_tree_with_runtime_assets(self) -> None:
        dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
        self.assertRegex(dockerfile, re.compile(
            r'^COPY(?:\s+--\S+)*\s+server/?\s+/app/server/?\s*$', re.MULTILINE),
            'Dockerfile 必须复制完整 server 树，而非仅复制 Python 文件')


if __name__ == '__main__':
    unittest.main()
