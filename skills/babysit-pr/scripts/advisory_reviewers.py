"""The Advisory Reviewer table shared by request-advisory-reviews and advisory-review-status.

Logins, marker comments and message text were read from ranokay/factory: the
REST reviews on #133, #145 and #150 show copilot-pull-request-reviewer[bot] and
greptile-apps[bot], issue comments show coderabbitai[bot], and the
review_requested timeline entries name the Copilot reviewer as `Copilot`.
CodeRabbit's "Review skipped" comment and Copilot's "insufficient remaining
quota" review are from #133. Greptile's summary marker and CodeRabbit's
generated-comment marker are from #133 and #150. CodeRabbit's "Review rate
limited" wording is from its status-check documentation; ranokay/factory has no
rate-limit comment.
"""

REVIEWERS = {
    "copilot": {
        "logins": ("copilot-pull-request-reviewer[bot]", "copilot"),
        "command": None,
        "marker": None,
        "summary_marker": None,
        "skip_phrases": ("insufficient remaining quota",),
    },
    "coderabbit": {
        "logins": ("coderabbitai[bot]",),
        "command": "@coderabbitai review",
        "marker": "<!-- factory:advisory-review:coderabbit -->",
        "summary_marker": None,
        "skip_phrases": ("review skipped", "review rate limited", "rate limit"),
    },
    "greptile": {
        "logins": ("greptile-apps[bot]", "greptile[bot]"),
        "command": "@greptileai",
        "marker": "<!-- factory:advisory-review:greptile -->",
        "summary_marker": "<!-- greptile_summary -->",
        "skip_phrases": (),
    },
}

PLACEHOLDERS = (
    "review in progress",
    "review queued",
    "currently processing new changes",
    "greptile is reviewing",
    "currently reviewing",
)


def normalize(login):
    return (login or "").lower().removesuffix("[bot]")


def is_reviewer(login, name):
    return normalize(login) in {normalize(item) for item in REVIEWERS[name]["logins"]}
