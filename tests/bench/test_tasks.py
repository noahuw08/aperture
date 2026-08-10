import json

from mcp_gateway_router.bench.tasks import Task, check_leakage, leakage, load_tasks


def _task(**kw):
    base = dict(task_id="t1", prompt="how many results", expect="1441792", match="number")
    base.update(kw)
    return Task(**base)


def test_number_match_ignores_separators():
    """The agent may answer 1,441,792 or 1441792 — both are the same answer."""
    task = _task()

    assert task.check("The total_count is 1,441,792.")
    assert task.check("1441792")
    assert not task.check("The total_count is 42.")


def test_exact_match_is_whitespace_and_case_insensitive_only():
    task = _task(expect="OK", match="exact")

    assert task.check("  ok  ")
    assert not task.check("ok then")


def test_contains_match_allows_surrounding_prose():
    task = _task(expect="feat/gateway", match="contains")

    assert task.check("The branch is feat/gateway-data-plane.")
    assert not task.check("The branch is main.")


def test_a_missing_answer_is_a_failure_not_an_error():
    """An arm that errored produces no answer; that must score 0, not raise."""
    assert not _task().check(None)


def test_leakage_measures_tool_vocabulary_reused_by_the_prompt():
    task = _task(
        prompt="search github code for a term and report the total count",
        required_tools=(("github", "search_code"),),
    )
    descriptions = {("github", "search_code"): "search code across github repositories"}

    # prompt reuses search / code / github → high overlap
    assert leakage(task, descriptions) > 0.4


def test_a_prompt_that_avoids_tool_words_scores_low():
    task = _task(
        prompt="how many public files mention modelcontextprotocol",
        required_tools=(("github", "search_code"),),
    )
    descriptions = {("github", "search_code"): "search code across github repositories"}

    assert leakage(task, descriptions) < 0.074


def test_check_leakage_flags_against_the_toolret_query_baseline():
    leaky = _task(
        task_id="leaky",
        prompt="search code across github repositories",
        required_tools=(("github", "search_code"),),
    )
    clean = _task(
        task_id="clean",
        prompt="how many public files mention modelcontextprotocol",
        required_tools=(("github", "search_code"),),
    )
    descriptions = {("github", "search_code"): "search code across github repositories"}

    flags = {r.task_id: r.leaks for r in check_leakage([leaky, clean], descriptions)}

    assert flags == {"leaky": True, "clean": False}


def test_a_task_with_no_required_tools_cannot_leak():
    assert leakage(_task(), {}) == 0.0


def test_tasks_round_trip_through_the_file_format(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "task_id": "prs",
                        "prompt": "which pull requests are open",
                        "expect": "3",
                        "match": "number",
                        "required_tools": [["github", "list_pull_requests"]],
                        "notes": "from session s-123",
                    }
                ]
            }
        )
    )

    (task,) = load_tasks(path)

    assert task.task_id == "prs"
    assert task.required_tools == (("github", "list_pull_requests"),)
    assert task.check("There are 3 open PRs.")
