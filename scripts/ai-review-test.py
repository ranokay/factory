import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class ReviewWorkflowTest(unittest.TestCase):
    def test_fork_guard_and_trigger(self):
        caller = (ROOT / '.github/workflows/ai-review-caller.yml').read_text()
        reusable = (ROOT / '.github/workflows/ai-review.yml').read_text()
        guard = 'github.event.pull_request.head.repo.full_name == github.repository'
        self.assertIn('pull_request:', caller)
        self.assertNotIn('pull_request_target', caller + reusable)
        self.assertIn(guard, caller)
        self.assertIn(guard, reusable)

    def test_fail_closed_and_posting_configuration(self):
        workflow = (ROOT / '.github/workflows/ai-review.yml').read_text()
        for required in (
            'secrets.OPENCODE_ZEN_API_KEY', 'exit 1', 'ocr_version: 1.14.0',
            "sticky_summary: 'true'", 'closingIssuesReferences',
            'issues: read', 'pull-requests: write',
        ):
            self.assertIn(required, workflow)


if __name__ == '__main__':
    unittest.main()
