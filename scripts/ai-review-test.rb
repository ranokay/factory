require 'yaml'
require 'minitest/autorun'
require 'open3'

class AiReviewTest < Minitest::Test
  ROOT = File.expand_path('..', __dir__)

  def workflow(name)
    YAML.safe_load_file(File.join(ROOT, '.github/workflows', name))
  end

  def test_fork_cannot_run_either_secret_job
    caller = workflow('ai-review-caller.yml')
    reusable = workflow('ai-review.yml')
    assert_equal ['pull_request'], caller.fetch(true).keys
    [caller, reusable].each do |file|
      condition = file.fetch('jobs').fetch('review').fetch('if')
      operands = condition.strip.split(/\s*==\s*/)
      assert_equal 2, operands.length, 'unsupported review job condition'
      evaluate = lambda do |head|
        context = {
          'github.event.pull_request.head.repo.full_name' => head,
          'github.repository' => 'ranokay/factory',
        }
        context.fetch(operands[0]) == context.fetch(operands[1])
      end
      assert evaluate.call('ranokay/factory')
      refute evaluate.call('outside/fork')
    end
  end

  def test_missing_key_fails_before_review
    steps = workflow('ai-review.yml').fetch('jobs').fetch('review').fetch('steps')
    check = steps.first
    assert_equal 'Require Console key', check.fetch('name')
    assert_equal '${{ secrets.OPENCODE_ZEN_API_KEY }}', check.fetch('env').fetch('ZEN_KEY')
    refute check.fetch('continue-on-error', false)
    assert_operator steps.index { |step| step['id'] == 'review' }, :>, 0
    _, _, missing = Open3.capture3({'ZEN_KEY' => ''}, 'bash', '-c', check.fetch('run'))
    _, _, present = Open3.capture3({'ZEN_KEY' => 'fixture'}, 'bash', '-c', check.fetch('run'))
    refute missing.success?
    assert present.success?
  end
end
