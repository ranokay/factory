import json
import tomllib
import unittest

from sync_upstream_fixtures import SyncFixture


class SyncUpstreamFiles(SyncFixture):
    def factory_repo(self):
        repo = self.init("factory")
        self.commit(repo, "mise.toml", 'go = "1.24"\n', "mise")
        self.commit(repo, "AGENTS.md", "# upstream\n", "agents")
        self.commit(repo, "GLOSSARY.md", "# glossary\n", "glossary")
        self.commit(repo, ".github/workflows/ci.yml", "on: push\n", "ci")
        base = self.git(repo, "rev-parse", "HEAD").stdout.strip()
        self.git(repo, "update-ref", "refs/remotes/upstream/main", base)
        return repo

    def branch(self, repo, name, filename, content, delete=False):
        self.git(repo, "switch", "-q", "main")
        self.git(repo, "switch", "-q", "-c", name)
        if delete:
            self.git(repo, "rm", "-q", filename)
        else:
            (repo / filename).parent.mkdir(parents=True, exist_ok=True)
            (repo / filename).write_text(content)
            self.git(repo, "add", filename)
        self.git(repo, "commit", "-qm", name)

    def factory_files(self, repo, branch):
        return self.cli("factory-files", "--from", "upstream/main", "--head", branch, cwd=repo, check=False)

    def test_factory_files_allow_upstream_edits_and_reject_factory_ones(self):
        repo = self.factory_repo()
        self.branch(repo, "add-hk", "hk.pkl", 'amends "upstream.pkl"\n')
        result = self.factory_files(repo, "add-hk")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hk.pkl: added Factory file", result.stderr)
        self.branch(repo, "profiles", "AGENTS.md", "# upstream\n\nProfiles: universal\n")
        self.assertIn("AGENTS.md: adds a Profiles: line", self.factory_files(repo, "profiles").stderr)
        self.branch(repo, "factory-include", "mise.toml", 'include = ["git::https://github.com/ranokay/factory.git//profiles/universal/mise.toml?ref=v0.2.0"]\n')
        self.assertIn("mise.toml: adds ranokay/factory", self.factory_files(repo, "factory-include").stderr)
        self.branch(repo, "plain-mise", "mise.toml", 'go = "1.25"\n')
        self.assertEqual(self.factory_files(repo, "plain-mise").returncode, 0)
        self.branch(repo, "workflow", ".github/workflows/gates.yml", "name: Gates\njobs:\n  gates:\n    uses: ranokay/factory/.github/workflows/gates.yml@main\n")
        self.assertIn(".github/workflows/gates.yml: adds ranokay/factory", self.factory_files(repo, "workflow").stderr)
        self.branch(repo, "yaml-workflow", ".github/workflows/gates.yaml", "name: Gates\njobs:\n  gates:\n    uses: ranokay/factory/.github/workflows/gates.yml@main\n")
        self.assertIn(".github/workflows/gates.yaml: adds ranokay/factory", self.factory_files(repo, "yaml-workflow").stderr)
        self.branch(repo, "workflow-edit", ".github/workflows/ci.yml", "on: push\njobs:\n  test:\n    uses: ranokay/factory/.github/workflows/gates.yml@main\n")
        self.assertIn(".github/workflows/ci.yml: adds ranokay/factory", self.factory_files(repo, "workflow-edit").stderr)
        self.branch(repo, "workflow-plain", ".github/workflows/ci.yml", "on: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n")
        self.assertEqual(self.factory_files(repo, "workflow-plain").returncode, 0)
        self.branch(repo, "delete", "GLOSSARY.md", "", delete=True)
        self.assertEqual(self.factory_files(repo, "delete").returncode, 0)

    def target(self, existing=None):
        path = self.root / "target"
        path.mkdir()
        for filename, content in (existing or {}).items():
            target = path / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        return path

    def test_install_files_copies_the_template_and_checks_clean(self):
        target = self.target()
        check = self.cli("install-files", "--target", str(target), "--check", check=False)
        self.assertEqual(check.returncode, 1)
        self.assertEqual(list(target.iterdir()), [])
        self.cli("install-files", "--target", str(target))
        for name in ("AGENTS.md", "mise.toml", "hk.pkl", "renovate.json", ".coderabbit.yaml", ".greptile/config.json"):
            self.assertTrue((target / name).is_file(), name)
        again = self.cli("install-files", "--target", str(target), "--check")
        self.assertIn("hk.pkl: upstream has its own; Factory Gates not amended", again.stdout)

    def test_install_files_fails_on_an_existing_file_with_no_merge_rule(self):
        template = self.root / "template"
        template.mkdir()
        (template / "extra.txt").write_text("factory\n")
        target = self.target({"extra.txt": "upstream\n"})
        result = self.cli("install-files", "--target", str(target), "--template", str(template), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no merge rule", result.stderr)
        self.assertEqual((target / "extra.txt").read_text(), "upstream\n")

    def test_install_files_requires_an_existing_template(self):
        target = self.target()
        result = self.cli("install-files", "--target", str(target), "--template", str(self.root / "missing"), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not exist", result.stderr)

    def test_install_files_adds_profiles_after_first_heading(self):
        template = self.root / "template"
        template.mkdir()
        (template / "AGENTS.md").write_text("# Factory\n\nProfiles: universal\n")
        target = self.target({"AGENTS.md": ""})
        original = b"\r\n# Title\r\n```text\r\nProfiles: fake\r\n```\r\n"
        (target / "AGENTS.md").write_bytes(original)
        self.cli("install-files", "--target", str(target), "--template", str(template))
        expected = original.replace(b"# Title\r\n", b"# Title\r\n\r\nProfiles: universal\r\n")
        self.assertEqual((target / "AGENTS.md").read_bytes(), expected)
        self.cli("install-files", "--target", str(target), "--template", str(template), "--check")

    def test_install_files_refuses_nonstandard_agents_preambles_without_writing(self):
        template = self.root / "template"
        template.mkdir()
        (template / "0.txt").write_text("new\n")
        (template / "AGENTS.md").write_text("# Factory\n\nProfiles: universal\n")
        target = self.target({"AGENTS.md": ""})
        for original in ("```\n# comment\n```\n# Title\n", "<pre>\n# comment\n</pre>\n# Title\n", "    code\n# Title\n", "  # Title\n", "Title\n=====\n", "---\ntitle: Example\n---\n# Title\n", "text\n# Title\n", "\n"):
            with self.subTest(original=original):
                (target / "AGENTS.md").write_text(original)
                result = self.cli("install-files", "--target", str(target), "--template", str(template), check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("install-files: AGENTS.md must begin with a # heading", result.stderr)
                self.assertEqual((target / "AGENTS.md").read_text(), original)
                self.assertFalse((target / "0.txt").exists())

    def test_install_files_scopes_coderabbit_enabled_to_auto_review(self):
        template = self.root / "template"
        template.mkdir()
        (template / ".coderabbit.yaml").write_text("reviews:\n  tools:\n    enabled: true\n  auto_review:\n    enabled: false\n")
        target = self.target({".coderabbit.yaml": "reviews:\n  auto_review:\n    other: true\n  tools:\n    enabled: true\n"})
        self.cli("install-files", "--target", str(target), "--template", str(template))
        text = (target / ".coderabbit.yaml").read_text()
        self.assertIn("auto_review:\n    enabled: false\n    other: true", text)
        self.assertIn("tools:\n    enabled: true", text)

    def test_install_files_preserves_inline_array_and_comment(self):
        template = self.root / "template"
        template.mkdir()
        (template / "mise.toml").write_text('include = ["factory"]\n[settings]\nexperimental = true\n')
        original = 'include = ["café.toml"] # preserve ] comment\n[settings]\nexperimental = true\n'
        target = self.target({"mise.toml": original})
        self.cli("install-files", "--target", str(target), "--template", str(template))
        self.assertEqual((target / "mise.toml").read_text(), original.replace('"café.toml"]', '"café.toml", "factory"]'))

    def test_install_files_preserves_block_arrays_and_adds_missing_tables(self):
        template = self.root / "template"
        template.mkdir()
        (template / "mise.toml").write_text('include = ["factory"]\n[task_config]\nincludes = ["factory-tasks"]\n[settings]\nexperimental = true\n')
        original = 'include = [ # existing ] comment\n  "other", # keep\n]\n[task_config]\nincludes = [\n  "tasks", # keep\n]\n'
        target = self.target({"mise.toml": original})
        self.cli("install-files", "--target", str(target), "--template", str(template))
        expected = original.replace('include = [ # existing ] comment\n', 'include = [ # existing ] comment\n  "factory",\n').replace('includes = [\n', 'includes = [\n  "factory-tasks",\n')
        self.assertEqual((target / "mise.toml").read_text(), expected + '\n[settings]\nexperimental = true\n')

    def test_install_files_preserves_crlf_and_commented_table_headers(self):
        template = self.root / "template"
        template.mkdir()
        (template / "mise.toml").write_text('include = ["factory"]\n[task_config]\nincludes = ["factory-tasks"]\n[settings]\nexperimental = true\n')
        original = b'include = ["old" # keep\r\n]\r\n[task_config] # keep\r\nincludes = ["tasks"] # keep\r\n[settings] # keep\r\nexperimental = true\r\n'
        target = self.target({"mise.toml": ""})
        (target / "mise.toml").write_bytes(original)
        self.cli("install-files", "--target", str(target), "--template", str(template))
        expected = original.replace(b'include = [', b'include = [\r\n  "factory",\r\n', 1).replace(b'"tasks"]', b'"tasks", "factory-tasks"]')
        self.assertEqual((target / "mise.toml").read_bytes(), expected)

    def test_install_files_adds_missing_task_and_settings_tables(self):
        template = self.root / "template"
        template.mkdir()
        (template / "mise.toml").write_text('include = ["factory"]\n[task_config]\nincludes = ["factory-tasks"]\n[settings]\nexperimental = true\n')
        target = self.target({"mise.toml": 'include = ["factory"]\n[tools]\npython = "3"\n'})
        self.cli("install-files", "--target", str(target), "--template", str(template))
        self.assertEqual((target / "mise.toml").read_text(), 'include = ["factory"]\n[tools]\npython = "3"\n\n[task_config]\nincludes = ["factory-tasks"]\n\n[settings]\nexperimental = true\n')

    def test_install_files_preserves_trailing_blank_lines_and_setting_comment(self):
        template = self.root / "template"
        template.mkdir()
        (template / "mise.toml").write_text('[task_config]\nincludes = ["factory-tasks"]\n[settings]\nexperimental = true\n')
        original = '[settings]\nexperimental = false # retain\n\n\n'
        target = self.target({"mise.toml": original})
        self.cli("install-files", "--target", str(target), "--template", str(template))
        self.assertEqual((target / "mise.toml").read_text(), original.replace("false", "true") + '\n[task_config]\nincludes = ["factory-tasks"]\n')

    def test_install_files_adds_key_after_header_without_final_newline(self):
        template = self.root / "template"
        template.mkdir()
        (template / "mise.toml").write_text('[task_config]\nincludes = ["factory-tasks"]\n[settings]\nexperimental = true\n')
        target = self.target({"mise.toml": '[task_config]'})
        self.cli("install-files", "--target", str(target), "--template", str(template))
        self.assertEqual((target / "mise.toml").read_text(), '[task_config]\nincludes = ["factory-tasks"]\n\n[settings]\nexperimental = true\n')

    def test_install_files_rejects_invalid_mise_shapes(self):
        template = self.root / "template"
        template.mkdir()
        (template / "mise.toml").write_text('include = ["factory"]\n[task_config]\nincludes = ["factory-tasks"]\n')
        target = self.target({"mise.toml": 'task_config = "invalid"\n'})
        for invalid in ('task_config = "invalid"\n', 'include = "invalid"\n', '[task_config]\nincludes = "invalid"\n'):
            with self.subTest(invalid=invalid):
                (target / "mise.toml").write_text(invalid)
                result = self.cli("install-files", "--target", str(target), "--template", str(template), check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("install-files: mise.toml", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_install_files_refuses_unsupported_mise_forms_without_writing(self):
        template = self.root / "template"
        template.mkdir()
        (template / "a.txt").write_text("new\n")
        (template / "mise.toml").write_text('include = ["factory"]\n[task_config]\nincludes = ["factory-tasks"]\n[settings]\nexperimental = true\n')
        target = self.target({"mise.toml": ""})
        for original, name, form in (('task_config = { includes = ["old"] }\n', "task_config", "inline table"), ('task_config.includes = ["old"]\n', "task_config", "dotted key"), ('task_config = [1, 2]\n', "task_config", "non-table value"), ('settings = []\n', "settings", "non-table value"), ('[[task_config]]\nincludes = ["old"]\n', "task_config", "array of tables"), ('[[task_config.sub]]\nx = 1\n', "task_config", "array of tables"), ('[task_config]\n[[task_config.includes]]\nx = 1\n', "task_config.includes", "array of tables"), ('[task_config.sub]\nincludes = ["old"]\n', "task_config", "dotted key"), ('"include" = ["old"]\n', "include", "quoted key"), ('[task_config]\n"includes" = ["old"]\n', "task_config.includes", "quoted key"), ('settings = { experimental = false }\n', "settings", "inline table"), ('[other]\nsettings = {}\n["settings"]\nexperimental = false\n', "settings", "quoted key"), ('["settings"]\nexperimental = false\n[[settings.other]]\nx = 1\n', "settings", "quoted key"), ('note = """\n[settings]\nexperimental = true\n"""\n["settings"]\nexperimental = true\n', "settings", "quoted key")):
            with self.subTest(original=original):
                (target / "mise.toml").write_text(original)
                result = self.cli("install-files", "--target", str(target), "--template", str(template), check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"install-files: mise.toml defines {name} as {form}; add the Factory entries by hand", result.stderr)
                self.assertEqual((target / "mise.toml").read_text(), original)
                self.assertFalse((target / "a.txt").exists())

    def test_install_files_rejects_non_utf8_without_writing(self):
        template = self.root / "template"
        template.mkdir()
        (template / "a.txt").write_text("new\n")
        (template / "mise.toml").write_text('include = ["factory"]\n')
        target = self.target({"mise.toml": ""})
        (target / "mise.toml").write_bytes(b"\xff")
        result = self.cli("install-files", "--target", str(target), "--template", str(template), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("text file is not valid UTF-8", result.stderr)
        self.assertIn(str(target / "mise.toml"), result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual((target / "mise.toml").read_bytes(), b"\xff")
        self.assertFalse((target / "a.txt").exists())

    def test_install_files_rejects_quoted_experimental_key(self):
        template = self.root / "template"
        template.mkdir()
        (template / "mise.toml").write_text('[settings]\nexperimental = true\n')
        original = '[settings]\n"experimental" = false\n'
        target = self.target({"mise.toml": original})
        result = self.cli("install-files", "--target", str(target), "--template", str(template), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("defines settings.experimental as quoted key", result.stderr)
        self.assertEqual((target / "mise.toml").read_text(), original)

    def test_invalid_utf8_names_template_and_body_files(self):
        template = self.root / "template"
        template.mkdir()
        source = template / "mise.toml"
        source.write_bytes(b"\xff")
        target = self.target()
        result = self.cli("install-files", "--target", str(target), "--template", str(template), check=False)
        self.assertIn(str(source), result.stderr)
        self.assertIn("text file is not valid UTF-8", result.stderr)
        self.assertEqual(list(target.iterdir()), [])
        repo = self.init("body-repo")
        head = self.commit(repo, "file", "content\n", "base")
        body = self.root / "body.md"
        body.write_bytes(b"\xff")
        result = self.cli("verdicts", "--from", head, "--to", head, "--body-file", str(body), cwd=repo, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(body), result.stderr)
        self.assertIn("text file is not valid UTF-8", result.stderr)

    def test_install_files_ignores_nested_arrays_when_finding_tables(self):
        template = self.root / "template"
        template.mkdir()
        (template / "mise.toml").write_text('include = ["factory"]\n[settings]\nexperimental = true\n')
        original = 'other = [\n  ["nested"]\n]\ninclude = ["old"]\n'
        target = self.target({"mise.toml": original})
        self.cli("install-files", "--target", str(target), "--template", str(template))
        expected = original.replace('"old"]', '"old", "factory"]') + '\n[settings]\nexperimental = true\n'
        self.assertEqual((target / "mise.toml").read_text(), expected)

    def test_install_files_accepts_whitespace_in_standard_headers(self):
        template = self.root / "template"
        template.mkdir()
        (template / "mise.toml").write_text('[task_config]\nincludes = ["factory-tasks"]\n[settings]\nexperimental = true\n')
        original = '[ task_config ]\nincludes = []\n[ settings ]\nexperimental = false # keep\n'
        target = self.target({"mise.toml": original})
        self.cli("install-files", "--target", str(target), "--template", str(template))
        expected = original.replace('includes = []', 'includes = ["factory-tasks"]').replace('false', 'true')
        self.assertEqual((target / "mise.toml").read_text(), expected)

    def test_install_files_extends_existing_files(self):
        target = self.target({
            "AGENTS.md": "# upstream\n\nOwn instructions.\n",
            "mise.toml": 'include = ["other.toml"]\n\n[tools]\ngo = "1.24"\n\n[settings]\nexperimental = false\n',
            "renovate.json": '{"extends":["config:base"],"rangeStrategy":"pin"}\n',
            ".greptile/config.json": '{"custom":true}\n',
            ".coderabbit.yaml": "reviews:\n  auto_review:\n    enabled: true\n  request_changes_workflow: false\n",
            "hk.pkl": 'amends "upstream.pkl"\n',
        })
        result = self.cli("install-files", "--target", str(target))
        self.assertIn("hk.pkl: upstream has its own; Factory Gates not amended", result.stdout)
        agents = (target / "AGENTS.md").read_text()
        self.assertTrue(agents.startswith("# upstream"))
        self.assertIn("Own instructions.", agents)
        self.assertIn("Profiles: universal", agents)
        mise = tomllib.loads((target / "mise.toml").read_text())
        self.assertEqual(mise["include"][0], "other.toml")
        self.assertTrue(any("ranokay/factory" in item for item in mise["include"]))
        self.assertIs(mise["settings"]["experimental"], True)
        self.assertTrue(any("ranokay/factory" in item for item in mise["task_config"]["includes"]))
        self.assertEqual(mise["tools"]["go"], "1.24")
        renovate = json.loads((target / "renovate.json").read_text())
        self.assertEqual(set(renovate["extends"]), {"config:base", "github>ranokay/factory"})
        self.assertEqual(renovate["rangeStrategy"], "pin")
        greptile = json.loads((target / ".greptile/config.json").read_text())
        self.assertIs(greptile["custom"], True)
        self.assertEqual(greptile["autoReview"], [])
        self.assertIs(greptile["statusCheck"], False)
        coderabbit = (target / ".coderabbit.yaml").read_text()
        self.assertIn("enabled: false", coderabbit)
        self.assertIn("request_changes_workflow: false", coderabbit)
        self.assertEqual((target / "hk.pkl").read_text(), 'amends "upstream.pkl"\n')
        self.cli("install-files", "--target", str(target), "--check")


if __name__ == "__main__":
    unittest.main()
